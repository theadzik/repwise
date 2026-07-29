"""The use cases: one module per command, and the report they print.

Each `run_*` function is the whole of a command apart from parsing and
dispatch. It is handed the things it needs - a Garmin session, a config, its
options - rather than building them, so nothing here imports argparse and
nothing here decides what a process exits with beyond returning it.
"""
