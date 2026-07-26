# Garmin Double Progression

Reads what you actually lifted from Garmin Connect and advances the targets in
your Garmin workouts automatically, using double progression.

Describe your routine in `workouts.yaml`, then run `workout update` after a
session. It works out the next target for every exercise, shows you the plan,
and writes it back to Garmin only when you approve.

- [Your routine](#your-routine) - where the plan lives
- [Progression rules](#progression-rules) - how targets advance
- [Tooling](#tooling) - setup, commands, configuration
- [How it works](#how-it-works) - architecture and data flow
- [Development](#development) - tests, schema notes, gotchas

## Your routine

Your routine lives in `workouts.yaml`: the exercises, rep ranges, set counts,
and which Garmin workout each belongs to. That file is gitignored; start from
[workouts.example.yaml](workouts.example.yaml), a complete working A/B full
body split annotated field by field. Use `--config` to keep it elsewhere.

You need the workouts already built in Garmin Connect. This tool updates their
targets; it does not create them. Take each `garmin_workout_id` from the
workout's URL in Garmin Connect.

## Progression rules

Double progression: reps go up first, weight second.

1. Start at the lower end of the range, e.g. `6-6-6` for a range of 6-10.
2. Each workout, add 1 rep to every set: `7-7-7`, then `8-8-8`.
3. Once every set reaches the upper end, add weight and reset to the lower end.
4. If you didn't match the previous result, repeat it unchanged.

`src/workout/progression.py` implements exactly these rules. Two refinements
matter in practice:

**Progress is judged by the weakest set.** The new rep target is
`min(reps) + 1`, not `previous target + 1`. Doing 7,7,10,10 against a target of
7 advances to 8, the same as 7,7,7,7 - extra reps on the easy sets earn nothing
while the floor stays put. But 8,8,8,8 advances to 9, and 10,10,10,10 tops out
the range and earns the weight jump even though only 7 was asked for.

**The weight actually lifted wins.** If you bump the load mid-session, that new
load is adopted rather than discarded. See
[working weight](#working-weight) below.

## Tooling

### Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp workouts.example.yaml workouts.yaml
```

That installs the package and a `workout` command; everything below can also be
run as `python -m workout` without installing.

`workouts.yaml` is gitignored, so your routine and Garmin ids stay out of
version control. Edit your copy to describe your own training - the example is
a complete, working A/B split, annotated field by field.

### Updating targets

The main command. Reads your latest logged session and advances the targets in
the matching Garmin workout.

```bash
workout update                    # dry run, shows changes
workout update --apply            # write to Garmin
workout update --apply --push     # and send them to your watch
workout update --dump             # save raw JSON, change nothing
workout update --activity 1234    # use a specific activity
workout --config other.yaml update
```

Run it in a real terminal the first time: it prompts for your Garmin email,
password, and MFA code. Credentials are never stored by this tool. On success
the OAuth tokens are cached in the configured token store and later runs skip
the prompt, which also avoids the rate-limited login endpoint.

**Dry run is the default.** Nothing is sent to Garmin without `--apply`.

Sample output:

```text
Activity: Workout B (1234567890)
Updating: Workout B -> workout 111111111

* Barbell Deadlift          10 x 60 kg  ->  6 x 65 kg    (hit 10 on every set, +5 kg and reset to 6)
* Dumbbell Lateral Raise      12 x 8 kg  ->  13 x 8 kg   (add 1 rep (12 -> 13))
  Sit-up                       11 reps  ->  11 reps      (missed target (10/11 on worst set), repeat)
  ! Standing Calf Raise: not found in the activity, skipped

Also in Workout A (workout 222222222):
* Standing Calf Raise         12 x 0 kg  ->  12 x 20 kg  (synced from Workout B)

Dry run: 3 step(s) would change. Re-run with --apply.
```

A leading `*` marks a step that would change. Lines starting with `!` are
warnings - exercises that were skipped and why.

Exit codes: `0` success, `1` nothing usable in the activity, `2` rate limited,
`3` bad configuration.

### Getting the change onto your watch

Editing a workout in Garmin Connect **does not reach the watch on its own**. A
plain device sync is not enough: the watch only collects a new copy when a
message is waiting for it, which is what the Connect app's "Send to Device"
button queues.

`--push` does that for you, for every workout the run wrote:

```bash
workout update --apply --push
```

```text
Wrote Workout B (111111111)

Queued 1 send(s) to Forerunner 945.
Sync your watch to pick up the new targets.
```

It requires `--apply` - without it nothing has been written, so there is
nothing to send - and refuses with exit code 3 if used alone.

By default every registered device gets a message. Restrict that with
`device_ids` under `settings.garmin` if you have more than one and only want
some of them to receive workouts.

Under the hood this POSTs a JSON array to
`/device-service/devicemessage/messages`. The endpoint is undocumented; the
payload was captured from Connect's own request, and `device_message()` in
`garmin/payloads.py` reproduces it. Note the body **must** be an array - a bare
object returns HTTP 500.

### Getting started from Garmin

Build the workouts in Garmin Connect first, then let the tool read them.

```bash
workout list                    # your strength workouts and their ids
workout list --all              # every workout, whatever the sport
workout import -o workouts.yaml # turn them into a config
```

`import` fills in everything Garmin actually knows and marks the rest `TODO`.
It writes to stdout unless `-o` is given, and refuses to overwrite an existing
file without `--force`. **It never modifies your config in place**, so your
comments and tuned values are safe.

Garmin knows less than this tool needs, so three things are inferred:

| Field | How |
|---|---|
| `rep_low` | Garmin's current target becomes the bottom of the range |
| `rep_high` | A suggestion: `rep_low` plus a few. **Check it** |
| `load` | Guessed from the exercise name (`BARBELL_*`, `DUMBBELL_*`, `CABLE_*`), else `machine` if loaded and `bodyweight` if not |

`rep_step`, `weight_step` and `video` have no Garmin equivalent at all and are
left to you. Everything else - `sets`, `rest`, `unit`, `garmin_name`,
`garmin_category`, `garmin_workout_id` - is read straight from the payload.

Select a subset with `--name` (substring, case-insensitive) or `--id`. Garmin's
API has no server-side name search, so filtering happens locally.

### Checking for drift

```bash
workout check
```

Compares your config against the Garmin workouts and reports where they
disagree: an exercise renamed in the Garmin app, a set count changed, an
exercise present in one but not the other.

Worth running occasionally, because a wrong `garmin_name` does not fail loudly -
matching falls back to `garmin_category`, so the run keeps working until the
fallback stops working too:

```text
Workout A (111111111)
   ! Standing Calf Raise: config says WEIGHTED_STANDING_CALF_RAISE, Garmin says
     STANDING_CALF_RAISE. Matched by category CALF_RAISE, so it works, but the
     name is wrong
```

Exits non-zero when it finds anything beyond a note, so it fits in a cron job.

### Fetching raw payloads

Downloads workout definitions as JSON. Mostly a connectivity check and a way to
inspect Garmin's schema by hand.

```bash
workout fetch                   # every workout in workouts.yaml
workout fetch 111111111         # a specific workout
```

### Configuration: `workouts.yaml`

The single source of truth: the routine, the Garmin workout ids, and every
setting. Nothing is configured anywhere else. Copy
`workouts.example.yaml` to `workouts.yaml` to get started; the example is
annotated in full.

```yaml
settings:
  garmin:
    token_store: ~/.garminconnect   # where OAuth tokens are cached
    activity_search_limit: 25       # recent activities scanned for a match
    dump_dir: .                     # where --dump and `fetch` write JSON
    device_ids: []                  # devices --push sends to; empty means all

  weight_steps:        # kg added when a range is topped out, by load type
    barbell: 2.5
    dumbbell: 1.0      # per dumbbell
    cable: 5.0
    machine: 5.0

workouts:
  - key: Workout A
    garmin_workout_id: "111111111"
    activity_prefixes: ["workout a", "training a"]
    exercises:
      - name: Barbell Back Squat
        garmin_name: BARBELL_BACK_SQUAT
        rep_low: 6
        rep_high: 10
        sets: 4
        rest: 120
        load: barbell
        video: https://www.youtube.com/watch?v=NK9Fqjco4iw
```

Workout fields:

| Field | Required | Meaning |
|---|:---:|---|
| `key` | yes | Workout name, must be unique |
| `garmin_workout_id` | yes | Garmin workout to update; the id in the Connect URL |
| `activity_prefixes` | yes | Lower-cased prefixes that match an activity name to this workout |
| `exercises` | yes | List of exercises, below |

Exercise fields:

| Field | Required | Default | Meaning |
|---|:---:|---|---|
| `name` | yes | | Label used in this tool's output |
| `garmin_name` | yes | | Exercise identifier Garmin reports and stores |
| `rep_low` | yes | | Bottom of the range; where each new weight starts |
| `rep_high` | yes | | Top of the range; clearing it on every set earns a weight jump |
| `sets` | yes | | Prescribed working sets |
| `rep_step` | no | `1` | Reps added when a target is met. Use `2` for exercises counted per side |
| `weight_step` | no | from `load` | kg added when the range is topped out, overriding the load type |
| `load` | yes | | `barbell`, `dumbbell`, `cable`, `machine`, or `bodyweight` |
| `rest` | no | `0` | Seconds between sets. Documentation only, not written to Garmin |
| `unit` | no | `reps` | `reps`, or `seconds` for timed holds like planks |
| `video` | no | `None` | Reference link. Documentation only |

`load` does real work: it selects the weight step from `settings.weight_steps`,
and `bodyweight` means the exercise never gains load. Any `load` other than
`bodyweight` must have an entry in `weight_steps`, unless the exercise sets its
own `weight_step`.

A per-exercise `weight_step` overrides the load type. The deadlift uses it: it
recruits far more musculature than the other barbell lifts, so novice programs
step it by 5 kg where a bench or curl gets 2.5 kg. Drop it back to 2.5 once
5 kg jumps start failing.

Loading validates and raises `ValueError` on: missing required fields, a `load`
with no weight step, a `weight_step` of zero or less, `rep_step` below 1,
`rep_low >= rep_high`, duplicate workout keys, and a missing
`garmin_workout_id`.

## How it works

### Layout

```text
workouts.yaml              all configuration: routine, Garmin ids, settings
src/workout/
    models.py              domain objects, no behaviour
    config.py              workouts.yaml -> models, with validation
    progression.py         the rules. No I/O, no Garmin types
    planner.py             match steps to exercises, decide the changes
    importer.py            Garmin workout -> config YAML
    checker.py             compare config against Garmin
    cli.py                 argument parsing and output
    garmin/
        client.py          authentication and the Garmin session
        payloads.py        Garmin's JSON <-> our types
tests/
    conftest.py            shared builders and fixtures
    test_progression.py    the rules
    test_config.py         loading and validation
    test_payloads.py       schema mapping, using trimmed real payloads
    test_planner.py        matching and planning
    test_importer.py       import and YAML rendering
    test_checker.py        drift detection
    test_cli.py            argument parsing and help
```

The dependencies run one way: `cli` -> `planner` -> `progression` -> `models`,
with `garmin` used only by `cli` and `planner`. Two boundaries carry the weight:

- **`progression.py` knows nothing about Garmin or YAML.** It takes a spec, a
  current target, and a list of performed sets. That is what makes the rules
  testable without a network.
- **`garmin/payloads.py` is the only module that knows Garmin's schema.** If
  Garmin changes their JSON, everything to fix is in that one file.

Only `planner.py` mutates a workout payload, and it performs no I/O, so a
caller can build a plan and throw it away. That is exactly what a dry run does,
which is why the dry run cannot accidentally write.

### Data flow

```text
workouts.yaml ---> load_workouts() ---> Workout / ExerciseSpec
                                                |
Garmin activity ---> performed_sets() ---> [PerformedSet]
                                                |
                                                v
Garmin workout ---> step_target() ---> Target ---> next_target() ---> Target
                                                                        |
                                                    apply_target() <----+
                                                          |
                                                          v
                                              PUT /workout-service/workout/{id}
```bash

Steps:

1. Load and validate `workouts.yaml`.
2. Authenticate, reusing cached tokens if present.
3. Find the latest activity whose name starts with one of the
   `activity_prefixes` (or use `--activity`).
4. Map it to a workout, and fetch both the activity's exercise sets and the
   Garmin workout definition.
5. For each step in the workout, match it to an exercise and compute the next
   target.
6. Print the plan. With `--apply`, PUT the mutated workout back.

### No state file

The scripts store nothing between runs. The Garmin workout holds the current
target, and the activity holds what was performed - together they answer
everything. This is why targets can be edited by hand in Garmin and the script
will simply pick up from there.

### Working weight

A session may use more than one load for the same exercise - for example
maxing out a set that felt light, then adding weight for the rest. Rather than
averaging, `working_weight()` takes the **most common load across the sets,
ties going to the heavier one**, and judges progression only among sets at that
load. A single lighter opening set therefore cannot drag the baseline back
down.

Everything then rebases onto that load, even if it differs from what the
Garmin workout still has stored.

### Decision order in `next_target()`

Given a spec, the currently stored `Target`, and the `PerformedSet`s:

| # | Condition | Result |
|---|---|---|
| 1 | No sets logged | Unchanged |
| 2 | Fewer than `sets` at the working weight | Bank the weight, consolidate reps |
| 3 | Same weight, floor below target | Repeat unchanged (rule 4) |
| 4 | Floor at or above `rep_high` | `rep_low` at weight + step (rule 3) |
| 5 | Otherwise | `floor + 1` at the working weight (rule 2) |

Bodyweight exercises never reach case 4's weight increase; they target
`rep_high` and hold.

### Alternating exercises

Rep ranges for unilateral work are conventionally written per side, but the
watch counts each side separately - it logs 20 for ten alternating lunges. You
can either correct the count after every session, or program the exercise in
Garmin's units and let the tool read the watch as-is.

For the second option, double the range (8-12 per leg becomes 16-24) and set
`rep_step: 2`. Without the step a plain +1 would advance only one side and leave
the target on an odd number; with it the ladder runs 16, 18, 20, 22, 24, then a
weight jump back to 16 - the same real rate, both sides even.

Keep `(rep_high - rep_low)` a multiple of `rep_step` so stepping lands exactly
on `rep_high` and earns the weight jump rather than straddling it. Should a
target ever sit off-step, the next one is capped at `rep_high` rather than
overshooting.

### Shared exercises

An exercise can appear in more than one workout - a calf raise in both days of
an A/B split, say. When a target moves, `plan_sync()` pushes the same target
into every other workout containing that exercise, so the copies cannot drift
apart. Those
workouts are fetched and written too, and the dry run lists them separately:

```text
Also in Workout A (workout 222222222):
* Standing Calf Raise    12 x 0 kg  ->  12 x 20 kg  (synced from Workout B)
```

Matching uses the same name-then-category rule as everywhere else, so the
`garmin_name` must agree between the two entries. Keep a shared exercise's
`rep_low`/`rep_high` identical in every workout; `load_config()` rejects the
file outright if they differ, since a synced target could land outside one
workout's range.

### Timed holds

An exercise with `unit: seconds` (the plank) progresses on time instead of
reps, using the same five rules - hold the target on every set and it goes up
by one second, fall short and it repeats.

The two payloads express this differently. The workout step ends on `time`
rather than `reps`, though the figure still lives in `endConditionValue`. The
activity records `repetitionCount: 1` per set and puts the real hold in
`duration`. `PerformedSet.as_time()` moves those seconds into `reps` at the
boundary, so `next_target()` needs no special case: a 47 s target held for
46 s reads as a missed rep and repeats.

Durations are rounded to whole seconds, since Garmin logs them as floats
(`46.0`, `48.072`).

Worked examples for a squat, range 6-10, 4 sets, 2.5 kg step, stored target
7 x 20 kg:

| Performed | Next target | Why |
|---|---|---|
| 7,7,7,7 @ 20 | 8 x 20 | Matched, add a rep |
| 7,7,10,10 @ 20 | 8 x 20 | Weakest set still 7 |
| 8,8,8,8 @ 20 | 9 x 20 | Beat it everywhere |
| 10,10,10,10 @ 20 | 6 x 22.5 | Topped the range |
| 7,7,7,5 @ 20 | 7 x 20 | Missed, repeat |
| 10 @ 20, then 8,8,8 @ 22.5 | 8 x 22.5 | Only 3 of 4 sets at 22.5, consolidate |
| 8,8,8,8 @ 22.5 | 9 x 22.5 | Rebased onto the heavier load |
| 8,8,8,8 @ 15 | 9 x 15 | Deload respected, not punished |

### Exercise matching

Names are normalised to letters and digits only, so `BARBELL_BACK_SQUAT` and
`Barbell Back Squat` collapse to the same key. `garmin_name` takes precedence,
with `name` as a fallback. Unmatched exercises are reported as warnings and
skipped - never silently ignored.

## Development

### Tests

```bash
.venv/bin/pip install -e ".[dev]"    # pytest, if not already installed
.venv/bin/python -m pytest -q
```

One test module per source module. No network access, so everything runs
offline: `test_payloads.py` works from trimmed copies of real Garmin
responses rather than live calls. `test_config.py` also validates
`workouts.example.yaml`, so a bad edit to the shipped example fails the suite.

### Garmin schema notes

Verified against real Garmin payloads. The field names below are what this
tool depends on; if Garmin changes them, `--dump` a session and correct
`src/workout/garmin/payloads.py`.

**The two payloads use different weight units.** This is the easiest thing to
get wrong:

| Payload | Field | Unit |
|---|---|---|
| Exercise set (activity) | `weight` | grams - `20000.0` means 20 kg |
| Workout step | `weightValue` | whatever `weightUnit` says, normally kilograms - `30.0` means 30 kg |

`step_weight_factor()` reads `weightUnit.factor` (grams per unit, 1000 for
kilograms) so both directions go through `value * factor / 1000`. Treating
`weightValue` as grams makes every target read as a 0.03 kg-style fraction.

Fields relied on:

| Payload | Field | Used for |
|---|---|---|
| Workout step | `exerciseName` | Matching to an exercise |
| Workout step | `category` | Fallback match, e.g. `SQUAT` |
| Workout step | `endCondition.conditionTypeKey` | `reps` normally, `time` for timed holds |
| Workout step | `endConditionValue` | Current and new rep target |
| Workout step | `weightValue`, `weightUnit` | Current and new load |
| Exercise set | `setType == "ACTIVE"` | Skipping rest sets |
| Exercise set | `repetitionCount` | Reps performed |
| Exercise set | `duration` | Seconds held, for timed exercises |
| Exercise set | `weight` | Load used, in grams |
| Exercise set | `exercises[0].name` | Which exercise the set belongs to |
| Exercise set | `exercises[0].category` | Fallback when the name differs or is null |
| Device message | `messageUrl`, `messageType`, `metaDataId` | Queueing a workout for the watch |

Sets are modelled as a `RepeatGroupDTO` with `numberOfIterations` wrapping one
executable step plus a rest step, so a workout holds one step per exercise, not
one per set. `iter_workout_steps()` walks into those groups; rest steps end on
`time` or `lap.button` and are skipped by `step_target()`.

> **Names disagree between the two payloads.** Garmin auto-detects the exercise
> while you lift, so what it logs need not be what the workout programs - a
> workout's `STANDING_ALTERNATING_DUMBBELL_CURLS` was logged as
> `SEATED_DUMBBELL_BICEPS_CURL`, and a triceps extension logged `name: null`.
> Categories survived both, which is why `garmin_category` exists and why
> matching falls back to it. A category is only used when exactly one exercise
> in the workout claims it.

### Finding your exercise identifiers

`garmin_name` and `garmin_category` must match what Garmin stores. Rather than
guess, dump a real session and read them off:

```bash
workout update --dump        # writes dump-workout-*.json and dump-sets-*.json
```

Each executable step in the workout dump carries `exerciseName` and `category`;
copy those into `workouts.yaml`. Anything that does not match is reported as a
`not in workouts.yaml` or `not found in the activity` warning rather than being
silently skipped.

Categories are not always the obvious ones. Some real examples:

| `garmin_name` | `garmin_category` |
|---|---|
| `LAT_PULLDOWN` | `PULL_UP` |
| `FACE_PULL_WITH_EXTERNAL_ROTATION` | `ROW` |
| `OVERHEAD_BARBELL_PRESS` | `SHOULDER_PRESS` |
| `WEIGHTED_LEG_CURL` | `LEG_CURL` |

Names also drift between the two payloads. A leg curl stored as
`WEIGHTED_LEG_CURL` in the workout gets logged as plain `LEG_CURL` in the
activity, and a curl programmed as `STANDING_ALTERNATING_DUMBBELL_CURLS` can be
logged as `SEATED_DUMBBELL_BICEPS_CURL`. In both cases only the category
bridges the two, which is why `garmin_category` is worth filling in.

### Making changes

- Changing the routine: edit `workouts.yaml`. Nothing else needs touching.
- Changing what a new user starts from: edit `workouts.example.yaml`, which
  the tests validate in place of anyone's private config.
- Changing progression: `next_target()` in `src/workout/progression.py`, plus
  `tests/test_progression.py`. It is pure, so no Garmin access is needed.
- Garmin changed its schema: everything to fix is in
  `src/workout/garmin/payloads.py`.
- Adding a setting: put it in `workouts.yaml` under `settings`, add the field to
  `GarminSettings` in `models.py`, and read it in `config.py`. Nothing should be
  hardcoded outside the config.
- Adding a load type: add it to `settings.weight_steps`, otherwise loading
  rejects any exercise using it. A single exercise can instead set its own
  `weight_step`.
- Adding a command: a subparser in `cli.py` plus a function taking
  `(args, config)`.
- Garmin's workout list API: `sportTypeKey` filters server-side and `orderBy`
  sorts, but there is no name search - `searchTerm`, `name`, `q` and friends are
  silently ignored, so name filtering must be done locally.

### Authentication and troubleshooting

`connect()` in `src/workout/garmin/client.py` resumes from the configured
`token_store` when possible and otherwise prompts. Delete that directory to
force a fresh login.

| Symptom | Cause and fix |
|---|---|
| `429` / rate limited | Too many login attempts from your IP. Wait; cached tokens avoid the login endpoint entirely |
| `401` after working before | Stale tokens. Delete `~/.garminconnect` and log in again |
| Cloudflare challenge | Only affects browser automation. These scripts use `garminconnect` (built on `curl_cffi`), which is not subject to it |
| Everything looks like bodyweight | The exercise-set weight field is misnamed. Check a `--dump` |

`garth` is deprecated and unmaintained after Garmin's March 2026 auth change.
`garminconnect` >= 0.3.5 rebuilt its login on `curl_cffi` and is the supported
path - do not pin below that.

### Known limitations

- `rest` and `video` are documentation only; they are not written back to
  Garmin. Only rep, time, and weight targets are.
- Workouts must already exist in Garmin Connect. This tool updates them, it
  does not create them.
- Every step of an exercise gets the same target, matching the "same reps on
  every set" model. Per-set targets are not supported.
- The first matching activity within `activity_search_limit` is used; older
  sessions need `--activity`.
- A deload rebases the stored target downward, so a bad day at a lighter weight
  moves the target with it.
