"""What the service is told through its environment."""

import os
from dataclasses import dataclass

#: What a run executes. Overridden in tests, which must never reach Garmin.
COMMAND = ("repwise", "update", "--apply", "--push")


@dataclass(frozen=True, kw_only=True)
class Settings:
    database_url: str
    #: Fernet keys, the one to encrypt with first. Any of them decrypts, which
    #: is how a key is replaced without a moment when nothing does.
    token_keys: tuple[str, ...]
    #: Where a run downloads workouts.yaml from, and the token that may read
    #: it. Only the worker needs them.
    config_url: str = ""
    github_token: str = ""
    #: Memory-backed in the cluster, so the decrypted Garmin session a run is
    #: given never reaches a disk.
    scratch_dir: str = "/scratch"
    #: The built web app. Empty when there is none to serve, as in the tests.
    static_dir: str = ""
    command: tuple[str, ...] = COMMAND
    run_timeout: int = 600

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=database_url(os.environ["DATABASE_URL"]),
            token_keys=tuple(os.environ["TOKEN_KEYS"].split()),
            config_url=os.environ.get("CONFIG_URL", ""),
            github_token=os.environ.get("GITHUB_TOKEN", ""),
            scratch_dir=os.environ.get("SCRATCH_DIR", "/scratch"),
            static_dir=os.environ.get("STATIC_DIR", ""),
        )


def database_url(url: str) -> str:
    """The URL with the driver named, which CloudNativePG's secret leaves out."""
    scheme, sep, rest = url.partition("://")
    if scheme in ("postgresql", "postgres"):
        return f"postgresql+psycopg{sep}{rest}"
    return url
