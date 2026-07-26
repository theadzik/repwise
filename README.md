# Garmin Double Progression

Reads what you actually lifted from Garmin Connect and advances the targets in
your Garmin workouts automatically, using double progression.

Train, run one command, sync your watch. Next session's numbers are already
waiting on it.

```text
$ workout update

Activity: Workout B (1234567890)
Updating: Workout B -> workout 111111111

* Barbell Deadlift          10 x 60 kg  ->  6 x 65 kg    (hit 10 on every set, +5 kg and reset to 6)
* Dumbbell Lateral Raise      12 x 8 kg  ->  13 x 8 kg   (add 1 rep (12 -> 13))
  Sit-up                       11 reps  ->  11 reps      (missed target (10/11 on worst set), repeat)

Dry run: 2 step(s) would change. Re-run with --apply.
```

## Quick start

You need workouts already built in Garmin Connect, and Python 3.11 or newer.

```bash
git clone https://github.com/theadzik/workout.git
cd workout
python3 -m venv .venv
.venv/bin/pip install -e .

workout import -o workouts.yaml   # build a config from your Garmin workouts
workout update                    # after a session: see what would change
workout update --apply --push     # write it, and send it to your watch
```

The first command that reaches Garmin asks for your email, password and MFA
code, then caches a token so later runs do not. Credentials are never stored by
this tool.

**Nothing is written to Garmin without `--apply`.** A dry run is the default.

The full walkthrough is in [Getting started](docs/getting-started.md).

## What it does

After each session it compares what you performed against the target stored in
Garmin and picks the next one:

1. Start at the bottom of the rep range.
2. Add a rep to every set each session.
3. Once every set reaches the top of the range, add weight and reset to the
   bottom.
4. If you missed the target, repeat it unchanged.

Two things it handles that a spreadsheet would not:

- **It judges progress by your weakest set.** Extra reps on the easy sets do not
  pull the target somewhere you cannot repeat.
- **It follows the weight you actually used.** Bump the load mid-session and the
  new load is banked, not discarded.

It also covers timed holds like planks, exercises the watch counts per side, and
keeping an exercise in sync when it appears in more than one workout.
[Progression](docs/progression.md) explains every decision it can make.

## Commands

| Command | What it does |
|---|---|
| `workout update` | Advance targets from your last session. Dry run by default |
| `workout update --apply --push` | Write the new targets and send them to your watch |
| `workout list` | Show your Garmin workouts and their ids |
| `workout import` | Build a `workouts.yaml` from your Garmin workouts |
| `workout check` | Report where your config and Garmin disagree |
| `workout fetch` | Download raw workout JSON |

Full flags and output for each are in [Commands](docs/commands.md).

## Your routine

Your routine lives in `workouts.yaml`: the exercises, rep ranges, set counts,
and which Garmin workout each belongs to. Generate it with `workout import`, or
copy [workouts.example.yaml](workouts.example.yaml) - a complete working A/B
full body split, annotated field by field.

That file is gitignored, so your routine and Garmin ids stay out of version
control. Every field is described in [Configuration](docs/configuration.md).

## Documentation

For users:

| Page | Contents |
|---|---|
| [Getting started](docs/getting-started.md) | Install to first update, step by step |
| [Commands](docs/commands.md) | Every command, flag, output marker and exit code |
| [Configuration](docs/configuration.md) | The `workouts.yaml` reference, and finding your exercise identifiers |
| [Progression](docs/progression.md) | How the next target is decided, with worked examples |
| [Troubleshooting](docs/troubleshooting.md) | Login problems, skipped exercises, known limitations |

For contributors:

| Page | Contents |
|---|---|
| [Architecture](docs/architecture.md) | Module layout, dependency and data-flow diagrams |
| [Garmin's API](docs/garmin-api.md) | The undocumented payloads and endpoints, and their traps |
| [Contributing](docs/contributing.md) | Dev setup, tests, where to make a change |

## Licence

[MIT](LICENSE).
