# Getting started

From nothing to your first automatic target update.

- [1. Build the workouts in Garmin](#1-build-the-workouts-in-garmin)
- [2. Install](#2-install)
- [3. Create your config](#3-create-your-config)
- [4. Log in](#4-log-in)
- [5. Do a session, then dry run](#5-do-a-session-then-dry-run)
- [6. Apply, and push to the watch](#6-apply-and-push-to-the-watch)
- [Where to go next](#where-to-go-next)

## 1. Build the workouts in Garmin

Build your routine in Garmin Connect first, as strength workouts with the
exercises, sets and reps you want. Starting there is the easier path: `workout
import` then writes your config for you, exercise identifiers and all.

You can skip this and describe the workouts in the config instead, leaving
`garmin_workout_id` out so that `workout update --apply` builds them in Garmin.
That means naming every exercise by its Garmin identifier by hand, which is
fiddlier the first time round. See [creating a
workout](commands.md#creating-a-workout).

Name them so the activities they produce are recognisable later, for example
"Workout A" and "Workout B". Matching works on the *activity* name, and an
activity started from a workout inherits that workout's name.

## 2. Install

```bash
git clone https://github.com/theadzik/workout.git
cd workout
python3 -m venv .venv
.venv/bin/pip install -e .
```

That gives you a `workout` command inside the virtualenv. Everything also works
as `python -m workout` if you would rather not install.

Python 3.14 or newer is required. `garminconnect` only asks for `>=3.12`; the
higher floor is this project's own, so that the source can use deferred
annotation evaluation and argparse's colour and did-you-mean output.

## 3. Create your config

The quickest route is to let the tool read what you already built:

```bash
workout list                     # your strength workouts and their ids
workout import -o workouts.yaml  # turn them into a config
```

`import` fills in everything Garmin knows and marks the rest `TODO`. Three
fields cannot be read and are inferred, so **check them**: `rep_high`,
`rep_low`, and `load`. See [commands](commands.md#import) for exactly what is
guessed.

Alternatively start from the annotated example and fill it in by hand:

```bash
cp workouts.example.yaml workouts.yaml
```

Either way, open `workouts.yaml` and set the rep ranges you actually want to
train. Every field is described in [configuration](configuration.md).

`workouts.yaml` is gitignored, so your routine and Garmin ids stay out of
version control.

## 4. Log in

The first command that reaches Garmin prompts for your credentials:

```text
Garmin email: you@example.com
Garmin password (hidden):
MFA code: 123456
```

Run it in a real terminal so you can type them. Credentials are never stored by
this tool. On success the OAuth tokens are cached in `~/.garminconnect` and
later runs skip the prompt entirely, which also avoids Garmin's rate-limited
login endpoint.

## 5. Do a session, then dry run

Train, and let the watch sync. Then:

```bash
workout update
```

This reads the most recent matching activity for each workout in your config,
compares what you actually lifted against the targets currently stored in
Garmin, and prints what it would change:

```text
Activity: Workout B (1234567890)
Updating: Workout B -> workout 111111111

* Barbell Deadlift          10 x 60 kg  ->  6 x 65 kg    (hit 10 on every set, +5 kg and reset to 6)
* Dumbbell Lateral Raise      12 x 8 kg  ->  13 x 8 kg   (add 1 rep (12 -> 13))
  Sit-up                       11 reps  ->  11 reps      (missed target (10/11 on worst set), repeat)
  ! Standing Calf Raise: not found in the activity, skipped

Dry run: 2 step(s) would change. Re-run with --apply.
```

**Nothing has been written.** A dry run is the default, and `update` cannot
write without `--apply`.

Read the reasons in brackets. If a target moved in a way you did not expect,
[progression](progression.md) explains every decision the tool can make. If an
exercise was skipped, the name in your config probably does not match Garmin's;
see [configuration](configuration.md#finding-your-exercise-identifiers).

## 6. Apply, and push to the watch

Once the plan looks right:

```bash
workout update --apply --push
```

`--apply` writes the new targets to Garmin Connect. `--push` then queues them
for your watch, because **editing a workout does not reach the watch on its
own** - a plain sync is not enough. Sync your watch afterwards to pick them up.

```text
Wrote Workout B (111111111)

Queued 1 send(s) to your last-used device.
Sync your watch to pick up the new targets.
```

That is the whole loop: train, `workout update --apply --push`, sync, train
again.

## Where to go next

- [Commands](commands.md) - every command and flag in full
- [Configuration](configuration.md) - the `workouts.yaml` reference
- [Progression](progression.md) - how the next target is decided
- [Troubleshooting](troubleshooting.md) - when something goes wrong
