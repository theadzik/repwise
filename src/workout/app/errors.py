"""What the process exits with.

Documented in docs/commands.md, so these are part of the interface: a script
wrapping this tool relies on them.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_NOTHING_USABLE = 1
EXIT_RATE_LIMITED = 2
EXIT_CONFIG = 3
