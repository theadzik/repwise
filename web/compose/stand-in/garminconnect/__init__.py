"""Stands in for garminconnect in the local stack. It signs nobody in anywhere.

The password is "right". An email starting "mfa@" is asked for a code, and the
code is "123456".
"""

import json
from types import SimpleNamespace


class Garmin:
    def __init__(self, email: str, password: str, *, return_on_mfa: bool) -> None:
        self.email = email
        self.password: str | None = password
        self.return_on_mfa = return_on_mfa
        token = {"di_token": "t", "di_refresh_token": "r", "di_client_id": "c"}
        self.client = SimpleNamespace(dumps=lambda: json.dumps(token))

    def login(self) -> tuple[str | None, None]:
        if self.password != "right":
            raise RuntimeError("Wrong password")
        return ("needs_mfa" if self.email.startswith("mfa@") else None), None

    def resume_login(self, _state: object, code: str) -> None:
        if code != "123456":
            raise RuntimeError("Wrong code")
