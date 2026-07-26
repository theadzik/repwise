# Contributing

- [Development setup](#development-setup)
- [Tests](#tests)
- [Where to make a change](#where-to-make-a-change)
- [Conventions](#conventions)

Read [architecture](architecture.md) first for the module layout, and
[Garmin's API](garmin-api.md) before touching anything that talks to Garmin.

## Development setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp workouts.example.yaml workouts.yaml
```

Dependencies are pinned exactly in `pyproject.toml`. It is an application
rather than a library, so a reproducible install matters more than being
co-installable, and Dependabot raises a PR per release.

## Tests

```bash
.venv/bin/python -m pytest -q
```

One test module per source module. **No network access**, so the whole suite
runs offline: `test_payloads.py` works from trimmed copies of real Garmin
responses rather than live calls, and there is no fixture that needs an account.

`test_config.py` also validates `workouts.example.yaml`, so a bad edit to the
shipped example fails the suite. It cannot validate anyone's real
`workouts.yaml`, since that file is gitignored and absent from a fresh checkout.

Adding a rule to `progression.py` needs no Garmin access at all - it takes plain
data and returns plain data, which is the point of keeping it pure.

## Where to make a change

| Change | Where |
|---|---|
| The routine | `workouts.yaml`. Nothing else needs touching |
| What a new user starts from | `workouts.example.yaml`, which the tests validate |
| A progression rule | `next_target()` in `progression.py`, plus `test_progression.py` |
| Garmin changed its schema | `garmin/payloads.py` only |
| A new setting | `workouts.yaml` under `settings`, the field on `GarminSettings` in `models.py`, and reading it in `config.py`. Nothing should be hardcoded outside the config |
| A new load type | `settings.weight_steps`, otherwise loading rejects any exercise using it. A single exercise can instead set its own `weight_step` |
| A new command | A subparser in `cli.py` plus a function taking `(args, config)` |
| A new Garmin call | A method on `GarminSession` in `garmin/client.py`, so the `garminconnect` dependency stays in one place |

## Conventions

- **Configuration lives in `workouts.yaml`.** If you find yourself adding a
  constant that a user might want to change, it belongs there instead.
- **Keep the domain pure.** `progression.py` and `models.py` must not import
  Garmin types, `yaml`, or anything that does I/O.
- **Warn, do not silently skip.** An exercise that cannot be matched is
  reported. Silent skips are how a wrong `garmin_name` hides for months.
- **Writes need `--apply`.** Anything new that changes remote state should
  default to describing what it would do.
- Prose in Markdown wraps at 80 columns; tables and code blocks are exempt and
  `.markdownlint.json` encodes that.
