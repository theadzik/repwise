#!/usr/bin/env bash
# The checks of ../compose/check.sh, against the repwise-test namespace once
# it is up as README.md has it, in the current kubectl context. It restarts and
# kills pods there, and nothing outside it.
#
#   web/test-cluster/check.sh
#
# Nothing here reaches Garmin: the namespace runs the stand-ins.
set -euo pipefail

k() { kubectl -n repwise-test "$@"; }
base=http://127.0.0.1:8088

k port-forward service/repwise-proxy 8088:8080 >/dev/null 2>&1 &
forward=$!
# Tolerant: a port-forward that already died must not fail a run that passed.
trap 'kill $forward 2>/dev/null || true' EXIT

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
worker_pod() { k get pods -l app=repwise-worker -o name; }

# The Garmin login, through the proxy like a browser: the password, then a
# wrong code and the right one on a new connection, as a phone that dropped the
# first sends them. Run inside an api pod, which has a WebSocket client.
garmin_login() {
    k exec -i deploy/repwise-api -c api -- python - <<'EOF'
import json
from websockets.sync.client import connect

LOGIN = "ws://repwise-proxy:8080/api/v1/garmin/connection/login"
with connect(LOGIN, origin="http://repwise-proxy:8080") as socket:
    socket.send(json.dumps({"email": "mfa@example.com", "password": "right"}))
    said = json.loads(socket.recv())
    assert said["step"] == "code_required"
with connect(LOGIN, origin="http://repwise-proxy:8080") as socket:
    socket.send(json.dumps({"resume": said["resume"], "code": "000000"}))
    assert json.loads(socket.recv())["step"] == "code_rejected"
    socket.send(json.dumps({"code": "123456"}))
    assert json.loads(socket.recv()) == {"step": "connected"}
print("connected")
EOF
}

up

section "Only the proxy reaches the API"
# The API believes whatever X-Forwarded-User it is sent, so this is what keeps
# anyone else from saying who they are.
reached=$(k exec deploy/repwise-worker -- python -c '
import urllib.request
try:
    urllib.request.urlopen("http://repwise-api:8080/healthz", timeout=5)
    print("reached")
except OSError:
    print("blocked")
')
expect "another pod in the namespace is turned away" "$reached" blocked

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
# Each replica asked directly, from the proxy's pod, which the network policy
# lets through: a Service picks a replica at random, so asking through it
# proves nothing about the other one.
run=$(body POST /api/v1/runs | field id)
seen=0
for ip in $(k get pods -l app=repwise-api -o jsonpath='{.items[*].status.podIP}'); do
    got=$(k exec deploy/repwise-proxy -- wget -qO- --header 'X-Forwarded-User: tester' \
        "http://$ip:8080/api/v1/runs/$run" | field id)
    [[ "$got" == "$run" ]] && seen=$((seen + 1))
done
expect "a run made through the proxy is read from each replica" "$seen" 2
expect "a second run is refused while the first is going" "$(status POST /api/v1/runs)" 409
wait_for "$run" succeeded 60
expect "the run says what repwise printed" "$(run_field "$run" output | grep -c '^done$')" 1

section "A restart forgets nothing"
k rollout restart deployment/repwise-api >/dev/null
k rollout status deployment/repwise-api >/dev/null
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
old=$(worker_pod)
# What a rollout does to the old pod: SIGTERM, then the 630-second grace.
k delete "$old" --wait=false >/dev/null
k wait --for=delete "$old" --timeout=120s >/dev/null
expect "the worker finished its run before it stopped" "$(run_field "$run" status)" succeeded
k rollout status deployment/repwise-worker >/dev/null

section "A dead worker is noticed"
run=$(body POST /api/v1/runs | field id)
wait_for "$run" running 30
# One second between SIGTERM and SIGKILL, and the run takes eight.
k delete "$(worker_pod)" --grace-period=1 >/dev/null
k rollout status deployment/repwise-worker >/dev/null
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
rows=$(k exec repwise-db-1 -c postgres -- psql -d app -tAc 'SELECT count(*) FROM garmin_connection')
expect "a session is stored" "$rows" 1
readable=$(k exec repwise-db-1 -c postgres -- pg_dump -d app | grep -c di_refresh_token || true)
expect "a dump of the database does not show the token" "$readable" 0
emptydir=$(k exec deploy/repwise-worker -- python -c '
import os
print(sum(len(files) for _, _, files in os.walk("/scratch")))
')
expect "nothing is left in the worker's scratch" "$emptydir" 0

section "All checks passed."
