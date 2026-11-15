#!/usr/bin/env bash
# Runs the checks PLAN.md lists under "How it is checked" against the local
# stack: two API replicas and a worker that share nothing but PostgreSQL.
#
#   web/compose/check.sh          # build, start, check, tear down
#   KEEP=1 web/compose/check.sh   # leave the stack up at http://127.0.0.1:8088
#
# Nothing here reaches Garmin: see compose/stand-in.
set -euo pipefail
cd "$(dirname "$0")/.."

base=http://127.0.0.1:8088

status() { curl -s -o /dev/null -w '%{http_code}' -X "$1" -H 'Content-Type: application/json' "$base$2"; }
body() { curl -s -X "$1" -H 'Content-Type: application/json' "$base$2"; }
field() { python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
newest() { body GET /api/v1/runs | python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["id"])'; }
run_field() { body GET "/api/v1/runs/$1" | field "$2"; }

section() { printf '\n%s\n' "$*"; }
expect() {
    if [[ "$2" == "$3" ]]; then
        printf '  ok    %s\n' "$1"
    else
        printf '  FAIL  %s: got "%s", expected "%s"\n' "$1" "$2" "$3"
        exit 1
    fi
}
wait_for() {
    local deadline=$((SECONDS + $3))
    until [[ "$(run_field "$1" status)" == "$2" ]]; do
        if ((SECONDS >= deadline)); then
            printf '  FAIL  run %s is "%s" after %ss, expected "%s"\n' \
                "$1" "$(run_field "$1" status)" "$3" "$2"
            exit 1
        fi
        sleep 1
    done
}
up() { until [[ "$(status GET /healthz)" == 200 ]]; do sleep 1; done; }

# The Garmin login, through the proxy like a browser: a wrong code, then the
# right one. Run inside a container, which has a WebSocket client.
garmin_login() {
    docker compose exec -T api-1 python - <<'EOF'
import json
from websockets.sync.client import connect

with connect("ws://proxy/api/v1/garmin/connection/login", origin="http://proxy") as socket:
    socket.send(json.dumps({"email": "mfa@example.com", "password": "right"}))
    assert json.loads(socket.recv()) == {"step": "code_required"}
    socket.send(json.dumps({"code": "000000"}))
    assert json.loads(socket.recv())["step"] == "code_rejected"
    socket.send(json.dumps({"code": "123456"}))
    assert json.loads(socket.recv()) == {"step": "connected"}
print("connected")
EOF
}

section "Starting the stack"
docker compose down --volumes --remove-orphans >/dev/null 2>&1
docker compose up --detach --build --quiet-pull >/dev/null 2>&1
up
[[ -n "${KEEP:-}" ]] || trap 'docker compose down --volumes >/dev/null 2>&1' EXIT

section "An account"
expect "nobody is registered at first" "$(status GET /api/v1/me)" 404
expect "registering creates the account" "$(status PUT /api/v1/me)" 201
expect "registering again does nothing" "$(status PUT /api/v1/me)" 200
expect "a change not sent as JSON is refused" \
    "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$base/api/v1/runs")" 415
expect "the web app is served" "$(curl -s "$base/" | grep -c '<div id="root">')" 1

section "The Garmin session"
expect "no run without one" "$(status POST /api/v1/runs)" 409
expect "signing in, with a wrong code first" "$(garmin_login)" connected
expect "the session is held" "$(status GET /api/v1/garmin/connection)" 200

section "Any replica answers"
run=$(body POST /api/v1/runs | field id)
replicas=$(for _ in 1 2 3 4; do
    curl -s -o /dev/null -D - "$base/api/v1/runs/$run" | tr -d '\r' |
        awk 'tolower($1) == "x-replica:" { print $2 }'
done | sort -u | wc -l)
expect "a run made through one replica is read through both" "$replicas" 2
expect "a second run is refused while the first is going" "$(status POST /api/v1/runs)" 409
wait_for "$run" succeeded 60
expect "the run says what repwise printed" "$(run_field "$run" output | grep -c '^done$')" 1

section "A restart forgets nothing"
docker compose restart api-1 api-2 >/dev/null 2>&1
up
expect "the newest run is still the one just made" "$(newest)" "$run"
expect "and still shown as succeeded" "$(run_field "$run" status)" succeeded

section "One run at a time"
answers=$({
    status POST /api/v1/runs &
    status POST /api/v1/runs &
    wait
} | fold -w 3 | sort | paste -sd ' ')
expect "two presses at once make one run" "$answers" "201 409"
wait_for "$(newest)" succeeded 60

section "A rollout does not kill a run"
run=$(body POST /api/v1/runs | field id)
wait_for "$run" running 30
docker compose stop worker >/dev/null 2>&1
expect "the worker finished its run before it stopped" "$(run_field "$run" status)" succeeded
docker compose start worker >/dev/null 2>&1

section "A dead worker is noticed"
run=$(body POST /api/v1/runs | field id)
wait_for "$run" running 30
docker compose kill --signal KILL worker >/dev/null 2>&1
docker compose start worker >/dev/null 2>&1
wait_for "$run" failed 120
expect "the run is failed, and says why" "$(run_field "$run" error)" "Worker lost"
expect "the button works again" "$(status POST /api/v1/runs)" 201
wait_for "$(newest)" succeeded 60

section "Signing out of Garmin"
run=$(body POST /api/v1/runs | field id)
wait_for "$run" running 30
expect "signing out mid-run" "$(status DELETE /api/v1/garmin/connection)" 204
wait_for "$run" succeeded 60
expect "the run did not put the session back" "$(status GET /api/v1/garmin/connection)" 404
expect "no run without a session" "$(status POST /api/v1/runs)" 409

section "The session at rest"
garmin_login >/dev/null
rows=$(docker compose exec -T db psql -U repwise -tAc 'SELECT count(*) FROM garmin_connection')
expect "a session is stored" "$rows" 1
readable=$(docker compose exec -T db pg_dump -U repwise repwise | grep -c di_refresh_token || true)
expect "a dump of the database does not show the token" "$readable" 0

section "All checks passed."
