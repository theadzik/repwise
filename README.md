# Garmin Double Progression

Reads what you actually lifted from Garmin Connect and advances the targets in
your Garmin workouts automatically, using double progression.

Train, run one command, sync your watch. Next session's numbers are already
waiting on it.

```text
$ repwise update

Activity: Workout B (1234567890)
Updating: Workout B -> workout 111111111

* Barbell Deadlift          10 x 60 kg  ->  6 x 65 kg    (hit 10 on every set, +5 kg and reset to 6)
* Dumbbell Lateral Raise      12 x 8 kg  ->  13 x 8 kg   (add 1 rep (12 -> 13))
  Sit-up                       11 reps  ->  11 reps      (missed target (10/11 on worst set), repeat)

Dry run: 2 step(s) would change. Re-run with --apply.
```

## Quick start

You need workouts already built in Garmin Connect, and Python 3.14 or newer.

```bash
git clone https://github.com/theadzik/repwise.git
cd repwise
python3 -m venv .venv
.venv/bin/pip install -e .

repwise import -o workouts.yaml   # build a config from your Garmin workouts
repwise update                    # after a session: see what would change
repwise update --apply --push     # write it, and send it to your watch
```

The first command that reaches Garmin asks for your email, password and MFA
code, then caches the tokens Garmin issues so later runs do not. **Your
password is never written anywhere. The token is** - in
`settings.garmin.token_store` (defaulting to `~/.config/repwise`), readable
only by you - **and until it expires it is as good as being logged in.**
`repwise logout` deletes it. See [authentication][troubleshooting-auth].

**Nothing is written to Garmin without `--apply`.** A dry run is the default.

The full walkthrough is in [Getting started][getting-started].

## What it does

It compares what you performed against the target stored in Garmin and picks
the next one. Every workout in your config is advanced from its own latest
session, so training A and then B and running once brings both up to date:

1. Start at the bottom of the rep range.
2. Add a rep to every set each session.
3. Once every set reaches the top of the range, add weight and reset to the
   bottom.
4. If you missed the target, repeat it unchanged.
5. A load only counts once you can carry it for the bottom of the range.

Three things it handles that a spreadsheet would not:

- **It judges progress by your weakest set.** Extra reps on the easy sets do not
  pull the target somewhere you cannot repeat.
- **It eases you back after a stall.** Miss a target twice and the session that
  finally beats it earns a rep on two of your four sets rather than on all of
  them, so you are not asked again for the jump that just failed.
- **It follows the weight you actually used.** Bump the load mid-session and the
  new load is banked, not discarded.
- **It won't keep a load you didn't earn.** Take the 4 kg dumbbells because the
  3 kg pair was gone, come up short of the range, and your target stays where it
  was rather than following you onto a weight that was too heavy.

Each exercise also gets a one-line note on its Garmin step, such as
`6-10 reps | +5 kg`, so the watch shows what you are working towards and not
just today's target. Notes you wrote yourself are left alone.

## Your config is the workout

`workouts.yaml` decides what a workout **is**; Garmin keeps track of where each
exercise has **got to**. Edit the file and the next run brings Garmin into
line:

- **Write a workout that does not exist yet.** Leave `garmin_workout_id` out
  and it is built in Garmin, then the id is written back into your file.
- **Reorder the exercises.** The order in the file is the order on the watch.
  Add one and it is added; delete one and it is deleted.
- **Set the rests and the sets.** Including the rest *between* exercises, which
  Garmin leaves as a wait for the lap button until you ask for a time.

An exercise that moves keeps everything it had, target included, because the
step itself is moved rather than rebuilt. That is the difference between
reordering your workout and quietly restarting your progression.

Nothing reaches Garmin without `--apply`, and a dry run prints every addition,
removal and move first.

It also covers timed holds like planks, exercises the watch counts per side, and
keeping an exercise in sync when it appears in more than one workout.
[Progression][progression] explains every decision it can make.

## Commands

| Command | What it does |
| --- | --- |
| `repwise update` | Advance targets from the latest session, and bring every workout in line with the config. Dry run by default |
| `repwise update --apply --push` | Write all of that to Garmin and send it to your watch |
| `repwise list` | Show your Garmin workouts and their ids |
| `repwise import` | Build a `workouts.yaml` from your Garmin workouts |
| `repwise check` | Check that your config names exercises Garmin actually has, that it still names the ones your workouts hold, and that every rep range fits what its weight step is really worth |
| `repwise fetch` | Download raw workout JSON |
| `repwise fetch exercises` | Download Garmin's list of every exercise it knows, which `check` and `update` read |
| `repwise logout` | Delete the cached Garmin token, so the next run logs in again |

Any command takes `-v` to show debug output as well. Full flags and output for
each are in [Commands][commands].

## Your routine

Your routine lives in `workouts.yaml`: the exercises, their order, rep ranges,
set counts, rests, and which Garmin workout each belongs to. Generate it with
`repwise import`, or copy [workouts.example.yaml][workouts-example] - a
complete working A/B full body split, annotated field by field.

It is the source of truth rather than a copy of one: `update` writes what it
says to Garmin, and the only thing ever written back into it is an id Garmin
issues for a workout it has just created.

That file is gitignored, so your routine and Garmin ids stay out of version
control. Every field is described in [Configuration][configuration].

## Documentation

For users:

| Page | Contents |
| --- | --- |
| [Getting started][getting-started] | Install to first update, step by step |
| [Commands][commands] | Every command, flag, output marker and exit code |
| [Configuration][configuration] | The `workouts.yaml` reference, and finding your exercise identifiers |
| [Progression][progression] | How the next target is decided, with worked examples |
| [Troubleshooting][troubleshooting] | Login problems, skipped exercises, known limitations |

For contributors:

| Page | Contents |
| --- | --- |
| [Architecture][architecture] | Module layout, dependency and data-flow diagrams |
| [Garmin's API][garmin-api] | The undocumented payloads and endpoints, and their traps |
| [Contributing][contributing] | Dev setup, tests, where to make a change |
| [Releasing][releasing] | Commit message format, and how the version is derived from it |

## Licence

[MIT][license].

[getting-started]: https://github.com/theadzik/repwise/blob/main/docs/getting-started.md
[commands]: https://github.com/theadzik/repwise/blob/main/docs/commands.md
[configuration]: https://github.com/theadzik/repwise/blob/main/docs/configuration.md
[progression]: https://github.com/theadzik/repwise/blob/main/docs/progression.md
[troubleshooting]: https://github.com/theadzik/repwise/blob/main/docs/troubleshooting.md
[troubleshooting-auth]: https://github.com/theadzik/repwise/blob/main/docs/troubleshooting.md#authentication
[architecture]: https://github.com/theadzik/repwise/blob/main/docs/architecture.md
[garmin-api]: https://github.com/theadzik/repwise/blob/main/docs/garmin-api.md
[contributing]: https://github.com/theadzik/repwise/blob/main/docs/contributing.md
[releasing]: https://github.com/theadzik/repwise/blob/main/docs/releasing.md
[workouts-example]: https://github.com/theadzik/repwise/blob/main/workouts.example.yaml
[license]: https://github.com/theadzik/repwise/blob/main/LICENSE
