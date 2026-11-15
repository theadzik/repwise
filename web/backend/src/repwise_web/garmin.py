"""The Garmin session: keeping it, handing it to a run, and signing in for one.

What garminconnect stores is a small JSON document holding a refresh token,
which is as good as being signed in. Here it is one encrypted row. The key is
not in the database, so a copy of the database alone cannot use it.
"""

import uuid
from collections.abc import Sequence

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
    """One sign-in to Garmin, alive for as long as its WebSocket is open.

    garminconnect signs in in two steps when Garmin wants a code, and holds a
    live HTTP session between them that cannot be saved and restored. So this
    object is never stored: it lives in the one connection that carries both
    steps, and is discarded with it.
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
