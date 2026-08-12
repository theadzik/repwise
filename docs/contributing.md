# Contributing

- [Development setup](#development-setup)
- [Checks](#checks)
- [Tests](#tests)
- [Where to make a change](#where-to-make-a-change)
- [Commit messages](#commit-messages)
- [Conventions](#conventions)

Read [architecture](architecture.md) first for the module layout, and
[Garmin's API](garmin-api.md) before touching anything that talks to Garmin.

## Development setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e . --group dev
.venv/bin/pre-commit install
cp workouts.example.yaml workouts.yaml
```

Two halves to that install: `-e .` for repwise and its runtime dependencies,
`--group dev` for the tools that check it. The tooling is a [PEP
735](https://peps.python.org/pep-0735/) dependency group rather than a `[dev]`
extra because an extra is part of the published package - anyone could
`pip install repwise[dev]`, and the release workflow could not install
commitizen without building repwise first. Groups are local to the repository
and install on their own. It needs pip 25.1 or newer, which every Python 3.14
ships with.

Dependencies are pinned exactly in `pyproject.toml`. It is an application
rather than a library, so a reproducible install matters more than being
co-installable, and Dependabot raises a PR per release.

## Checks

`pre-commit install` wires up three git hooks: the checks below on every commit,
the message format on `commit-msg`, and the test suite on push.

| Hook | What it does |
| --- | --- |
| `ruff-check --fix` | Lints and fixes what is mechanically fixable. Rules in `[tool.ruff.lint]` |
| `ruff-format` | Formats. 88 columns, the same defaults as Black |
| `mypy` | Type checks `src` and `tests`. Settings in `[tool.mypy]` |
| `markdownlint --fix` | Markdown, using `.markdownlint.json` |
| `codespell` | Typos, in prose and code alike |
| `check-yaml`, `check-toml` | The config files parse |
| `check-dependabot`, `check-github-workflows` | GitHub will actually accept `dependabot.yml` and the workflows |
| `detect-private-key`, `check-added-large-files` | Accidents |
| `forbid-private-files` | Refuses to commit `workouts.yaml` or a raw dump, which `.gitignore` covers but `git add -f` does not |
| `commitizen` | The commit message is conventional. See [releasing](releasing.md) |
| `pytest` | On push only, so a failing test does not block saving work in progress |

Every tool reads its settings from `pyproject.toml`, so a bare `ruff check`,
`mypy` or `pytest` behaves exactly as the hook does - which is also how
`.github/workflows/ci.yml` runs them on a pull request, over the whole tree
rather than the staged files. That run is a required check, so it is what
decides whether a pull request can merge; the hooks are the same checks moved
earlier, where they are cheaper to act on.

Run the lot without committing:

```bash
.venv/bin/pre-commit run --all-files
```

Invoke it as `.venv/bin/pre-commit`: that puts the virtualenv first on `PATH`,
which is how the `mypy` and `pytest` hooks find the project's dependencies. A
globally installed `pre-commit` will not find them.

Hooks that fix rather than report - `ruff-format`, `markdownlint`,
`end-of-file-fixer` - abort the commit when they change a file. Stage the
change and commit again; the second attempt passes. `git commit --no-verify`
skips the lot when you need it to.

Two pins per tool have to stay in step: `rev:` in `.pre-commit-config.yaml` and
the exact version in `pyproject.toml`. `pre-commit autoupdate` moves the first,
Dependabot the second.

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

Adding a rule to `domain/progression.py` needs no Garmin access at all - it
takes plain data and returns plain data, which is the point of keeping it pure.

## Where to make a change

| Change | Where |
| --- | --- |
| The routine | `workouts.yaml`. Nothing else needs touching |
| What a new user starts from | `workouts.example.yaml`, which the tests validate |
| A progression rule | `next_target()` in `domain/progression.py`, plus `test_progression.py` |
| Garmin changed its schema | `garmin/payloads.py` only, both halves of it: what reads a payload and what builds one |
| A new field on a workout or exercise | The field on `domain/models.py`, reading it in `config.py`, applying it in `planner.py`, and a key in the `render_*` mapping in `importer.py` so a round trip keeps it |
| How the config file is read or written | `yamlio.py`, which every other module goes through |
| A new setting | `workouts.yaml` under `settings`, the field on `GarminSettings` in `domain/models.py`, and reading it in `config.py`. Nothing should be hardcoded outside the config |
| A new load type | `settings.weight_steps`, otherwise loading rejects any exercise using it. A single exercise can instead set its own `weight_step` |
| A new command | A module in `app/` exposing `run_<name>()`, a subparser in `cli/parser.py`, and an entry in `HANDLERS` in `cli/__init__.py` |
| How an exercise is recognised | `domain/matching.py`, which the planner and the checker share |
| A new failure the user should see | A class in `errors.py` carrying its `exit_code`. Raise it; `main()` already prints it and exits with it |
| A new Garmin call | A method on `GarminSession` in `garmin/client.py`, so the `garminconnect` dependency stays in one place |

## Commit messages

Conventional Commits, enforced by the `commit-msg` hook installed above:

```text
fix(progression): stop a deload rebasing the target upward
```

The type decides the next version number, so it is not decoration: `feat` is a
minor release, `fix` a patch, and `docs` or `chore` release nothing. The full
list, and how a release is cut, is in [releasing](releasing.md).

## Conventions

- **Configuration lives in `workouts.yaml`.** If you find yourself adding a
  constant that a user might want to change, it belongs there instead.
- **Keep the domain pure.** Nothing in `domain/` may import Garmin types,
  `yaml`, or anything that does I/O.
- **A use case is handed what it needs.** Modules in `app/` take a session, a
  config and their options as arguments; only `cli/` constructs those, which
  is what lets a command be tested without a network or a parser.
- **Warn, do not silently skip.** An exercise that cannot be matched is
  reported. Silent skips are how a wrong `garmin_name` hides for months.
- **Writes need `--apply`.** Anything new that changes remote state should
  default to describing what it would do.
- Prose in Markdown wraps at 80 columns; tables and code blocks are exempt and
  `.markdownlint.json` encodes that.
