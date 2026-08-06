"""Reading and writing workouts.yaml, as YAML.

Every interaction this tool has with that file goes through here: `load_config`
validates what `read` returns, `record_workout_id` writes back through `dump`
and `write`, and `workout import` renders a new file with the same `dump`. One
place decides how the file is parsed, how what we write is styled, and how it
is put on disk.

The file is data, not text. An earlier version edited it line by line to spare
the comments a YAML round trip discards; that bought careful text handling in
two modules, and the comments go now.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any

import yaml

from .errors import ConfigError

__all__ = ["dump", "read", "write"]


class _Dumper(yaml.SafeDumper):
    """PyYAML's SafeDumper, with sequences indented under their key.

    Left alone it puts the dashes of a list in the same column as the key
    above them, which is valid YAML that nobody writes by hand. This file is
    read and edited by the user, so it comes out looking like the example.
    """

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def dump(data: Any) -> str:
    """`data` as the YAML this tool writes.

    Keys keep the order they are in rather than being sorted, so a document
    read from the file and written back keeps the order it was written in. The
    width is set past anything a config holds, because the default folds a long
    line - a note, a URL - across two, which is legal and unreadable.
    """
    return yaml.dump(
        data,
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=10**6,
    )


def read(path: str) -> Any:
    """The parsed document at `path`.

    A file that cannot be read or parsed is a configuration problem like any
    other, so it leaves here as one rather than as a traceback from yaml.
    """
    try:
        with open(path) as fh:
            return yaml.safe_load(fh)
    except OSError as exc:
        raise ConfigError(f"{path} could not be read: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc


def write(path: str, text: str) -> None:
    """Put `text` in the file, or leave the file exactly as it was.

    Written beside the destination and moved onto it, rather than opening the
    destination for writing - which truncates it before a byte is written, and
    would leave a config in pieces if the run died in between.

    That matters more than the odds suggest: one caller writes an id Garmin has
    just issued, so a half-written config would cost the routine and the id
    that stops the next run creating the workout a second time.
    """
    directory = os.path.dirname(path) or "."
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=directory,  # the same filesystem, or the move would not be atomic
            prefix=f".{os.path.basename(path)}.",
            delete=False,
        ) as fh:
            temporary = fh.name
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())  # on disk before the move, not just written

        if os.path.exists(path):
            shutil.copymode(path, temporary)  # keep whatever permissions it had
        os.replace(temporary, path)
    except OSError as exc:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
        raise ConfigError(f"{path} could not be written: {exc}") from exc
