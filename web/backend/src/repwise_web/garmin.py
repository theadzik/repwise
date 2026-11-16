"""The Garmin session: keeping it, handing it to a run, and signing in for one.

What garminconnect stores is a small JSON document holding a refresh token,
which is as good as being signed in. Here it is one encrypted row. The key is
not in the database, so a copy of the database alone cannot use it.
"""

import secrets
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import garminconnect
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from repwise_web.db import GarminConnection


class TokenBox:
    """Encrypts with the first key, decrypts with any of them."""

    def __init__(self, keys: Sequence[str]) -> None:
        self._fernet = MultiFernet([Fernet(key) for key in keys])

    def seal(self, token: str) -> bytes:
        return self._fernet.encrypt(token.encode())

    def unseal(self, ciphertext: bytes) -> str:
        return self._fernet.decrypt(ciphertext).decode()


def load(db: Session, box: TokenBox, athlete_id: uuid.UUID) -> tuple[str, int] | None:
    """The session and the version it was read at, or None when signed out."""
    row = db.get(GarminConnection, athlete_id)
    if row is None:
        return None
    return box.unseal(row.token_ciphertext), row.version


def store_login(db: Session, box: TokenBox, athlete_id: uuid.UUID, token: str) -> None:
    """Keep the session a login has just produced, whatever was there before."""
    sealed = box.seal(token)
    db.execute(
        insert(GarminConnection)
        .values(athlete_id=athlete_id, token_ciphertext=sealed)
        .on_conflict_do_update(
            index_elements=[GarminConnection.athlete_id],
            set_={
                "token_ciphertext": sealed,
                "version": GarminConnection.version + 1,
                "connected_at": func.now(),
                "updated_at": func.now(),
            },
        )
    )


def store_refreshed(
    db: Session, box: TokenBox, athlete_id: uuid.UUID, token: str, *, seen: int
) -> bool:
    """Keep a session a run refreshed, unless the row has changed since.

    A login made while the run was going is the newer truth, and signing out
    deleted the row: neither is undone by a run that started before it.
    """
    result = db.execute(
        update(GarminConnection)
        .where(
            GarminConnection.athlete_id == athlete_id,
            GarminConnection.version == seen,
        )
        .values(
            token_ciphertext=box.seal(token),
            version=seen + 1,
            updated_at=func.now(),
        )
    )
    return bool(result.rowcount)


def forget(db: Session, athlete_id: uuid.UUID) -> None:
    """Sign out of Garmin: delete the only copy of the session held here.

    Garmin is not told. Its unofficial API has no call that ends a session,
    which is also true of `repwise logout`.
    """
    db.execute(
        delete(GarminConnection).where(GarminConnection.athlete_id == athlete_id)
    )


def is_connected(db: Session, athlete_id: uuid.UUID) -> bool:
    found = db.scalar(
        select(GarminConnection.athlete_id).where(
            GarminConnection.athlete_id == athlete_id
        )
    )
    return found is not None


class Login:
    """One sign-in to Garmin, in the two steps it takes when Garmin wants a code.

    garminconnect holds a live HTTP session between the steps that cannot be
    saved and restored, so this object never leaves the process. Between the
    steps it waits in `Waiting`.
    """

    def __init__(self, email: str, password: str) -> None:
        self._api = garminconnect.Garmin(email, password, return_on_mfa=True)

    def start(self) -> bool:
        """Send the password. True when Garmin wants a code to finish."""
        wants_code, _ = self._api.login()
        # Kept by the library for the lifetime of the object, which a login
        # waiting on its code would otherwise make as long as the connection's.
        self._api.password = None
        return bool(wants_code)

    def finish(self, code: str) -> None:
        """Send the code. A wrong one raises and leaves the login waiting."""
        self._api.resume_login(None, code)

    def token(self) -> str:
        return str(self._api.client.dumps())


@dataclass
class Held:
    """A login waiting on its code, and what it is allowed before it ends."""

    resume: str
    login: Login
    deadline: float
    codes_left: int


class Waiting:
    """Logins waiting on their code, at most one per athlete, for a while.

    Kept here rather than in the WebSocket that started them, because a phone
    closes the page's connection when it is left to read the code in the mail
    app. A new connection brings the code back with the `resume` it was given.
    This is memory in one replica: the ingress sends a browser back to the
    replica it used before, and a login that does not finish is gone after
    `ttl` seconds or with the pod.
    """

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._held: dict[uuid.UUID, Held] = {}

    def hold(self, athlete: uuid.UUID, login: Login, codes: int) -> str:
        """Keep a login until its code comes, replacing any older one."""
        self._expire()
        resume = secrets.token_urlsafe(16)
        self._held[athlete] = Held(resume, login, self._clock() + self._ttl, codes)
        return resume

    def claim(self, athlete: uuid.UUID, resume: str) -> Held | None:
        """Take the login out to send it a code, or None if it is gone."""
        self._expire()
        held = self._held.get(athlete)
        if held is None or not secrets.compare_digest(held.resume, resume):
            return None
        del self._held[athlete]
        return held

    def put_back(self, athlete: uuid.UUID, held: Held) -> None:
        """Return a login whose code was wrong, unless a newer one started."""
        self._held.setdefault(athlete, held)

    def _expire(self) -> None:
        now = self._clock()
        for athlete in [a for a, held in self._held.items() if held.deadline <= now]:
            del self._held[athlete]
