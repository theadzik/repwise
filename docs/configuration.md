# Configuration

`workouts.yaml` is the single source of truth: the routine, the Garmin workout
ids, and every setting. Nothing is configured anywhere else.

**The file drives the workout, not the other way round.** `update` builds a
workout Garmin does not have, orders the exercises the way the file does, adds
and removes them to match, and writes the sets and rests it declares. What it
does not decide is where each exercise has got to: the target lives in Garmin,
which is what a session moves. See [what the config
drives](commands.md#what-the-config-drives).

Start from [workouts.example.yaml](../workouts.example.yaml), which is a
complete working A/B split annotated field by field, or generate one with
[`workout import`](commands.md#import).

- [Where the file lives](#where-the-file-lives)
- [Settings](#settings)
- [Workout fields](#workout-fields)
- [Exercise fields](#exercise-fields)
- [Load and weight steps](#load-and-weight-steps)
- [Validation](#validation)
- [Finding your exercise identifiers](#finding-your-exercise-identifiers)

## Where the file lives

`--config PATH` names it outright. Otherwise the first of these that exists is
used:

| Order | Location | For |
| --- | --- | --- |
| 1 | `$WORKOUT_CONFIG` | Keeping it somewhere of your own, or switching routines |
| 2 | `./workouts.yaml` | Running in the directory that holds your routine |
| 3 | `$XDG_CONFIG_HOME/workout/workouts.yaml`, i.e. `~/.config/workout/workouts.yaml` | An installed copy, run from anywhere |
| 4 | The checkout this package is running from | Working on the tool itself |

The checkout is last so that a clone never shadows a config of your own, and
is skipped entirely when the package is installed rather than run from source.
When none of them exists, the error lists the paths it tried.

## Settings

```yaml
settings:
  garmin:
    token_store: ~/.garminconnect   # where OAuth tokens are cached
    activity_search_limit: 25       # recent activities scanned for a match
    dump_dir: .                     # where --dump and `fetch` write JSON

  weight_steps:        # kg added when a range is topped out, by load type
    barbell: 2.5
    dumbbell: 1.0      # per dumbbell
    cable: 5.0
    machine: 5.0
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `garmin.token_store` | `~/.garminconnect` | Where OAuth tokens are cached. Delete the directory to force a fresh login |
| `garmin.activity_search_limit` | `25` | How many recent activities to scan for a name match |
| `garmin.dump_dir` | `.` | Where `--dump` and `fetch` write JSON |
| `weight_steps` | - | kg added per load type when a rep range is topped out |

## Workout fields

```yaml
workouts:
  - key: Workout A
    garmin_workout_id: "111111111"
    activity_prefixes: ["workout a", "training a"]
    rest_between_exercises: 60
    exercises:
      - ...
```

| Field | Required | Meaning |
| --- | :---: | --- |
| `key` | yes | Workout name, must be unique. Also the name a created workout is given in Garmin |
| `garmin_workout_id` | no | Garmin workout to update; the id in the Connect URL. Leave it out and the workout is created, then the id is written back here |
| `activity_prefixes` | yes | Prefixes that match an activity name to this workout, compared case-insensitively |
| `rest_between_exercises` | no | Seconds to rest between exercises. Left out, Garmin's own wait for the lap button is kept |
| `exercises` | yes | List of exercises, below |

List every name your sessions might carry in `activity_prefixes`, including
other languages - an activity called "Trening A" matches `trening a`.

**The order of `exercises` is the order of the workout.** Move an entry and the
next run moves the exercise in Garmin; add or delete one and it is added or
deleted there. See [ordering, adding and
removing](commands.md#ordering-adding-and-removing).

### A workout Garmin does not have yet

Leave `garmin_workout_id` out and `update --apply` builds the workout, then
writes the id Garmin issued back into this file:

```yaml
  - key: Workout C
    activity_prefixes: ["workout c"]
    exercises:
      - name: Front Squat
        garmin_name: FRONT_SQUAT
        rep_low: 6
        rep_high: 10
        sets: 4
        load: barbell
        start_weight: 40      # where a created exercise begins
```

Every exercise starts at `rep_low` and its `start_weight`, and progression
takes over from the first session.

**This is the one time the tool writes to your config, and it rewrites the
whole file to do it.** The document is parsed, the id set under its `key`, and
the whole thing dumped back: every workout, exercise, value and unrecognised
key survives, in the order you wrote them. Comments and blank lines do not -
they are not part of the document. Keep anything worth saying in an exercise's
[`notes`](#exercise-fields), which is.

## Exercise fields

```yaml
      - name: Barbell Back Squat
        garmin_name: BARBELL_BACK_SQUAT
        garmin_category: SQUAT
        rep_low: 6
        rep_high: 10
        sets: 4
        rest: 120
        load: barbell
        notes: Bar high on the traps, knees over the toes.
```

| Field | Required | Default | Meaning |
| --- | :---: | --- | --- |
| `name` | yes | | Label used in this tool's output |
| `garmin_name` | yes | | Exercise identifier as stored in the Garmin workout |
| `garmin_category` | no | none | Garmin's category, used when the name does not match |
| `rep_low` | yes | | Bottom of the range; where each new weight starts |
| `rep_high` | yes | | Top of the range; clearing it on every set earns a weight jump |
| `sets` | yes | | Working sets, written to the Garmin workout by `update --apply` |
| `rep_step` | no | `1` | Reps added when a target is met. Use `2` for exercises counted per side |
| `load` | yes | | `barbell`, `dumbbell`, `cable`, `machine`, or `bodyweight` |
| `weight_step` | no | from `load` | kg added when the range is topped out, overriding the load type |
| `rest` | no | none | Seconds between sets, written to the Garmin workout by `update --apply`. Left out, Garmin's own rest is kept |
| `start_weight` | no | `0` | kg a **newly created** exercise starts at. Never read again once the step exists; progression owns the weight from then on |
| `unit` | no | `reps` | `reps`, or `seconds` for timed holds like planks |
| `notes` | no | none | Free text: a cue, a link, a reminder. Read by nobody - not this tool, not Garmin |

`garmin_category` is worth filling in even though it is optional: Garmin
sometimes logs a different name than the one programmed, and the category is
what bridges them. See [finding your exercise
identifiers](#finding-your-exercise-identifiers).

For `unit: seconds` the range is in seconds and progression adds time rather
than load. See [timed holds](progression.md#timed-holds).

For exercises the watch counts per side, see [alternating
exercises](progression.md#alternating-exercises).

`rep_low`, `rep_high`, `rep_step` and `weight_step` do double duty: besides
driving progression they are summarised into each Garmin step's notes field, so
the watch shows the range you are working through. Editing any of them is
enough to make the next run rewrite those notes. See [step
notes](commands.md#step-notes).

`sets` and `rest` are written to Garmin rather than merely described by it, so
changing either of them here changes the workout on your watch after the next
`update --apply`. A `rest` can only be retimed where Garmin already counts one
down; see [rest between sets](commands.md#rest-between-sets).

## Load and weight steps

`load` does real work: it selects the weight step from `settings.weight_steps`,
and `bodyweight` means the exercise never gains load. Any `load` other than
`bodyweight` must have an entry in `weight_steps`, unless the exercise sets its
own `weight_step`.

A per-exercise `weight_step` overrides the load type. A deadlift is the usual
case: it recruits far more musculature than the other barbell lifts, so novice
programs step it by 5 kg where a bench or curl gets 2.5 kg. Drop it back once
5 kg jumps start failing.

## Validation

The config is validated on load, and a bad file is rejected outright rather
than half-applied. You get an error naming the file and workout for:

- a missing required field
- a `load` with no matching entry in `weight_steps`
- a `weight_step` of zero or less, which would never progress
- `rep_step` below 1
- `rep_low >= rep_high`
- a duplicate workout `key`
- a negative `rest_between_exercises` or `start_weight`
- a workout with neither a `garmin_workout_id` nor any exercises, which is
  nothing to find in Garmin and nothing to build there either
- a [shared exercise](progression.md#shared-exercises) programmed with
  different rep ranges in different workouts

Every problem in the file is reported at once, rather than one per run:

```text
3 problems:
  - workouts.yaml:Workout A: 'Barbell Back Squat' has rep_low >= rep_high
  - workouts.yaml:Workout A: exercise is missing sets
  - workouts.yaml:Workout B: exercise 'Plank' has load 'kettlebell', which
    has no entry in settings.weight_steps
```

## Finding your exercise identifiers

`garmin_name` and `garmin_category` must match what Garmin stores. Rather than
guess, dump a real session and read them off:

```bash
workout update --dump   # writes dump-workout-*.json and dump-sets-*.json
```

Each executable step in the workout dump carries `exerciseName` and `category`;
copy those into `workouts.yaml`. [`workout check`](commands.md#check) finds any
that do not match, and is worth running after editing these by hand.

**Get `garmin_name` wrong and the exercise is treated as two.** The config
names one Garmin does not have, so it is built; the one Garmin has goes
unnamed, so it is removed - and the target stored in it goes with it. The
category bridges the two when it is filled in and unambiguous, which is the
best reason to fill it in.

Categories are not always the obvious ones. Some real examples:

| `garmin_name` | `garmin_category` |
| --- | --- |
| `LAT_PULLDOWN` | `PULL_UP` |
| `FACE_PULL_WITH_EXTERNAL_ROTATION` | `ROW` |
| `OVERHEAD_BARBELL_PRESS` | `SHOULDER_PRESS` |
| `WEIGHTED_LEG_CURL` | `LEG_CURL` |

Names also drift between the two payloads. A leg curl stored as
`WEIGHTED_LEG_CURL` in the workout gets logged as plain `LEG_CURL` in the
activity, and a curl programmed as `STANDING_ALTERNATING_DUMBBELL_CURLS` can be
logged as `SEATED_DUMBBELL_BICEPS_CURL`. In both cases only the category bridges
the two, which is why `garmin_category` is worth filling in.
