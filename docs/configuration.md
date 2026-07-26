# Configuration

`workouts.yaml` is the single source of truth: the routine, the Garmin workout
ids, and every setting. Nothing is configured anywhere else.

Start from [workouts.example.yaml](../workouts.example.yaml), which is a
complete working A/B split annotated field by field, or generate one with
[`workout import`](commands.md#import).

- [Settings](#settings)
- [Workout fields](#workout-fields)
- [Exercise fields](#exercise-fields)
- [Load and weight steps](#load-and-weight-steps)
- [Validation](#validation)
- [Finding your exercise identifiers](#finding-your-exercise-identifiers)

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
    exercises:
      - ...
```

| Field | Required | Meaning |
| --- | :---: | --- |
| `key` | yes | Workout name, must be unique |
| `garmin_workout_id` | yes | Garmin workout to update; the id in the Connect URL |
| `activity_prefixes` | yes | Prefixes that match an activity name to this workout, compared case-insensitively |
| `exercises` | yes | List of exercises, below |

List every name your sessions might carry in `activity_prefixes`, including
other languages - an activity called "Trening A" matches `trening a`.

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
        video: https://www.youtube.com/watch?v=NK9Fqjco4iw
```

| Field | Required | Default | Meaning |
| --- | :---: | --- | --- |
| `name` | yes | | Label used in this tool's output |
| `garmin_name` | yes | | Exercise identifier as stored in the Garmin workout |
| `garmin_category` | no | none | Garmin's category, used when the name does not match |
| `rep_low` | yes | | Bottom of the range; where each new weight starts |
| `rep_high` | yes | | Top of the range; clearing it on every set earns a weight jump |
| `sets` | yes | | Prescribed working sets |
| `rep_step` | no | `1` | Reps added when a target is met. Use `2` for exercises counted per side |
| `load` | yes | | `barbell`, `dumbbell`, `cable`, `machine`, or `bodyweight` |
| `weight_step` | no | from `load` | kg added when the range is topped out, overriding the load type |
| `rest` | no | `0` | Seconds between sets. Documentation only, not written to Garmin |
| `unit` | no | `reps` | `reps`, or `seconds` for timed holds like planks |
| `video` | no | none | Reference link. Documentation only |

`garmin_category` is worth filling in even though it is optional: Garmin
sometimes logs a different name than the one programmed, and the category is
what bridges them. See [finding your exercise
identifiers](#finding-your-exercise-identifiers).

For `unit: seconds` the range is in seconds and progression adds time rather
than load. See [timed holds](progression.md#timed-holds).

For exercises the watch counts per side, see [alternating
exercises](progression.md#alternating-exercises).

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
- a missing `garmin_workout_id`
- a [shared exercise](progression.md#shared-exercises) programmed with
  different rep ranges in different workouts

## Finding your exercise identifiers

`garmin_name` and `garmin_category` must match what Garmin stores. Rather than
guess, dump a real session and read them off:

```bash
workout update --dump   # writes dump-workout-*.json and dump-sets-*.json
```

Each executable step in the workout dump carries `exerciseName` and `category`;
copy those into `workouts.yaml`. Anything that does not match is reported as a
`not in workouts.yaml` or `not found in the activity` warning rather than being
silently skipped, and [`workout check`](commands.md#check) finds these for you.

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
