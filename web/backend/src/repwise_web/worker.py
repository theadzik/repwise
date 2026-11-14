"""Does the runs: claims a queued row, runs repwise, writes the result back.

repwise is run as the command it is, unchanged. It expects a home directory
with its config and its Garmin token store in it, so each run is given one,
filled from the database and deleted afterwards. Nothing is left in the pod.
"""

import logging
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, text, update

from repwise_web import garmin
from repwise_web.db import FAILED, RUNNING, SUCCEEDED, Run, Sessions, connect
from repwise_web.settings import Settings

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 10
#: A running row whose worker has said nothing for this long has lost it.
LOST_AFTER_SECONDS = 60
POLL_SECONDS = 2
#: The end of the output is where repwise says what it did.
MAX_OUTPUT = 1_000_000

_CLAIM = text(
    """
    UPDATE run
    SET status = 'running', started_at = now(), heartbeat_at = now(),
        claimed_by = :worker
    WHERE id = (
        SELECT id FROM run WHERE status = 'queued'
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id, athlete_id
    """
)
_REAP = text(
    """
    UPDATE run
    SET status = 'failed', error = 'Worker lost', finished_at = now()
    WHERE status = 'running'
      AND heartbeat_at < now() - make_interval(secs => :seconds)
    """
)


class Refused(Exception):
    """A run that cannot start, for a reason the page should show as it is."""


@dataclass(frozen=True)
class Outcome:
    status: str
    exit_code: int | None = None
    output: str = ""
    error: str | None = None


class Worker:
    def __init__(
        self, settings: Settings, sessions: Sessions, name: str | None = None
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.box = garmin.TokenBox(settings.token_keys)
        self.name = name or socket.gethostname()

    def run_one(self) -> bool:
        """Do the oldest queued run. False when there was none to do."""
        self.reap()
        claimed = self.claim()
        if claimed is None:
            return False
        run_id, athlete_id = claimed
        log.info("Run %s: started", run_id)
        outcome = self.execute(run_id, athlete_id)
        with self.sessions() as db, db.begin():
            db.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(
                    status=outcome.status,
                    exit_code=outcome.exit_code,
                    output=outcome.output[-MAX_OUTPUT:],
                    error=outcome.error,
                    finished_at=func.now(),
                )
            )
        log.info("Run %s: %s %s", run_id, outcome.status, outcome.error or "")
        return True

    def claim(self) -> tuple[uuid.UUID, uuid.UUID] | None:
        """Take the oldest queued run. No two workers are given the same one."""
        with self.sessions() as db, db.begin():
            row = db.execute(_CLAIM, {"worker": self.name}).first()
        return (row.id, row.athlete_id) if row else None

    def reap(self) -> int:
        """Fail the runs whose worker has stopped reporting.

        Failed, not queued again: a run that died may already have changed
        Garmin, and pressing the button again is for a person to decide.
        """
        with self.sessions() as db, db.begin():
            lost = db.execute(_REAP, {"seconds": LOST_AFTER_SECONDS}).rowcount
        if lost:
            log.warning("Marked %d run(s) as lost", lost)
        return int(lost)

    def execute(self, run_id: uuid.UUID, athlete_id: uuid.UUID) -> Outcome:
        """Do a claimed run. Whatever goes wrong, it comes back as an outcome.

        A run that was claimed and then abandoned would sit as running until
        the heartbeat rule gave up on it, with the button dead meanwhile.
        """
        beat = _Heartbeat(self.sessions, run_id)
        beat.start()
        home = None
        try:
            home = tempfile.mkdtemp(dir=self.settings.scratch_dir)
            return self._attempt(athlete_id, Path(home))
        except Refused as exc:
            return Outcome(FAILED, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - whatever it was, the page is where it gets read
            return Outcome(FAILED, error=f"{type(exc).__name__}: {exc}")
        finally:
            beat.stop()
            if home is not None:
                shutil.rmtree(home, ignore_errors=True)

    def _attempt(self, athlete_id: uuid.UUID, home: Path) -> Outcome:
        with self.sessions() as db:
            held = garmin.load(db, self.box, athlete_id)
        if held is None:
            raise Refused("Not signed in to Garmin. Sign in on the Garmin screen.")
        token, version = held

        document = fetch_config(self.settings)
        config = yaml.safe_load(document) or {}
        # repwise builds a workout that has no id and writes the id Garmin
        # issued back into the file. This copy is deleted with the run, so the
        # id would be lost with it and the next run would build the workout
        # again.
        new = without_garmin_id(config)
        if new:
            raise Refused(
                f"{', '.join(new)}: no garmin_workout_id yet. Run repwise where "
                "workouts.yaml is edited, so that the id is written into the "
                "file, and push it."
            )
        config_path = home / "workouts.yaml"
        config_path.write_bytes(document)
        store = token_file(config, home)
        _write_private(store, token)

        try:
            # Only what repwise needs. The worker's own environment holds the
            # database password and the key that encrypts every session.
            done = subprocess.run(
                self.settings.command,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": str(home),
                    "REPWISE_CONFIG": str(config_path),
                },
                # Without a terminal repwise explains a missing Garmin session
                # and exits, where it would otherwise wait on a login prompt.
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=self.settings.run_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            said = exc.stdout or ""
            outcome = Outcome(
                FAILED,
                output=said if isinstance(said, str) else said.decode(errors="replace"),
                error=f"Timed out after {self.settings.run_timeout} seconds",
            )
        else:
            outcome = Outcome(
                SUCCEEDED if done.returncode == 0 else FAILED,
                exit_code=done.returncode,
                output=done.stdout,
            )
        # However the run ended. repwise refreshes the session as it goes, and
        # a refresh token that was replaced is no use to the next run.
        self._keep_refreshed(athlete_id, store, token, version)
        return outcome

    def _keep_refreshed(
        self, athlete_id: uuid.UUID, store: Path, given: str, version: int
    ) -> None:
        try:
            now = store.read_text()
        except OSError:
            return
        if now == given:
            return
        with self.sessions() as db, db.begin():
            kept = garmin.store_refreshed(db, self.box, athlete_id, now, seen=version)
        log.info("Garmin session refreshed: %s", "kept" if kept else "superseded")


class _Heartbeat:
    """Says the run is still being worked on, until told to stop."""

    def __init__(self, sessions: Sessions, run_id: uuid.UUID) -> None:
        self._sessions = sessions
        self._run_id = run_id
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._beat, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        self._thread.join()

    def _beat(self) -> None:
        while not self._stopped.wait(HEARTBEAT_SECONDS):
            try:
                with self._sessions() as db, db.begin():
                    db.execute(
                        update(Run)
                        .where(Run.id == self._run_id, Run.status == RUNNING)
                        .values(heartbeat_at=func.now())
                    )
            except Exception:
                # A database that is briefly away must not end the run. If it
                # stays away, another worker marks the run lost.
                log.exception("Heartbeat failed")


def fetch_config(settings: Settings) -> bytes:
    """Download workouts.yaml.

    On every run, because repwise reshapes the Garmin workouts to match the
    file it is given: a copy gone stale here would undo an edit made since.
    """
    request = urllib.request.Request(
        settings.config_url,
        headers={
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github.raw+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return bytes(response.read())
    except OSError as exc:
        raise Refused(f"Could not download {settings.config_url}: {exc}") from exc


def without_garmin_id(config: dict[str, Any]) -> list[str]:
    """The keys of the workouts Garmin has not been asked to build yet."""
    return [
        str(workout.get("key"))
        for workout in config.get("workouts") or []
        if not workout.get("garmin_workout_id")
    ]


def token_file(config: dict[str, Any], home: Path) -> Path:
    """Where repwise will look for the Garmin tokens, given `home` as its home.

    workouts.yaml says where the token store is, as a directory or as the JSON
    file itself, and normally under `~`.
    """
    named = (config.get("settings") or {}).get("garmin") or {}
    store = str(named.get("token_store") or "~/.config/repwise")
    path = home / store[2:] if store.startswith("~/") else Path(store)
    return path if path.suffix == ".json" else path / "garmin_tokens.json"


def _write_private(path: Path, content: str) -> None:
    """As garminconnect writes it: 0600 in a 0700 directory, or repwise warns."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def main(settings: Settings) -> None:
    if not settings.config_url:
        raise SystemExit("CONFIG_URL is not set: there is no workouts.yaml to run.")
    stop = threading.Event()
    # A run in progress is finished before the process exits: the signal only
    # stops the next one from being claimed.
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker = Worker(settings, connect(settings.database_url))
    log.info("Worker %s: waiting for runs", worker.name)
    while not stop.is_set():
        try:
            busy = worker.run_one()
        except Exception:
            # The database being away is the likely cause, and it comes back.
            log.exception("Could not look for a run")
            busy = False
        if not busy:
            stop.wait(POLL_SECONDS)
    log.info("Worker %s: stopped", worker.name)
