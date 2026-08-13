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
[`repwise import`](commands.md#import).

- [Where the file lives](#where-the-file-lives)
- [Settings](#settings)
- [Workout fields](#workout-fields)
- [Exercise fields](#exercise-fields)
- [Load and weight steps](#load-and-weight-steps)
- [Does the range fit the step](#does-the-range-fit-the-step)
- [Validation](#validation)
- [Finding your exercise identifiers](#finding-your-exercise-identifiers)

## Where the file lives

`--config PATH` names it outright. Otherwise the first of these that exists is
used:

| Order | Location | For |
| --- | --- | --- |
| 1 | `$REPWISE_CONFIG` | Keeping it somewhere of your own, or switching routines |
| 2 | `./workouts.yaml` | Running in the directory that holds your routine |
| 3 | `$XDG_CONFIG_HOME/repwise/workouts.yaml`, i.e. `~/.config/repwise/workouts.yaml` | An installed copy, run from anywhere |
| 4 | The checkout this package is running from | Working on the tool itself |

The checkout is last so that a clone never shadows a config of your own, and
is skipped entirely when the package is installed rather than run from source.
When none of them exists, the error lists the paths it tried.

## Settings

```yaml
settings:
  garmin:
    token_store: ~/.config/repwise  # OAuth tokens, and the exercise catalog
    activity_search_limit: 50       # recent activities scanned for a match
    dump_dir: .                     # where `fetch` writes JSON
    activity_caching: false         # read sessions from dump_dir, and file new ones

  weight_steps:        # kg added when a range is topped out, by load type
    barbell: 2.5
    dumbbell: 1.0      # per dumbbell
    cable: 5.0
    machine: 5.0

  min_weights:         # the lightest each load type can go, by load type
    barbell: 12.0      # the smallest bar on the rack, not the standard 20 kg
    dumbbell: 1.0
    cable: 5.0
    machine: 5.0

  # bodyweight: 81.0   # normally read from your Garmin weigh-ins
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `garmin.token_store` | `$XDG_CONFIG_HOME/repwise`, i.e. `~/.config/repwise` | Where the OAuth tokens are cached, and where [`fetch exercises`](commands.md#fetch-exercises) caches the exercise catalog. Beside your config by default, so one directory is everything this tool owns. The token is as good as being logged in until it expires, so the directory is kept private to you - see [what is stored](troubleshooting.md#what-is-stored-and-what-it-is-worth). [`repwise logout`](commands.md#logout) empties it. Tokens already in `~/.garminconnect`, where the default used to point, are still used until you move them - [see upgrading](troubleshooting.md#upgrading-from-a-version-that-defaulted-to-garminconnect), which 2.0 stops doing |
| `garmin.activity_search_limit` | `50` | How many recent activities to scan for a name match, for the sessions behind it, and for the strength sessions [`fetch activities`](commands.md#fetch-activities) downloads |
| `garmin.dump_dir` | `.` | Where [`fetch`](commands.md#fetch) writes JSON, and what `garmin.activity_caching` reads back. Resolved against the directory you run repwise in, not against this file - so the default is one directory per place you run from |
| `garmin.activity_caching` | `false` | Answer for a performed session from `dump_dir` instead of asking Garmin again, and file every session a run sees. See [reusing what is on disk](#reusing-what-is-on-disk) |
| `weight_steps` | - | kg added per load type when a rep range is topped out |
| `min_weights` | none | The lightest each load type can go. A [deload](progression.md#deloading) stops here rather than prescribing a weight you cannot make up. A load type left out has no floor |
| `bodyweight` | your Garmin weigh-ins | Your weight in kg, when you would rather state it than have it read. Only ever an input to [`check`](#does-the-range-fit-the-step); no target depends on it |

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
| `min_weight` | no | from `load` | The lightest this exercise can be loaded, overriding the load type |
| `rest` | no | none | Seconds between sets, written to the Garmin workout by `update --apply`. Left out, Garmin's own rest is kept |
| `start_weight` | no | `0` | kg a **newly created** exercise starts at. Never read again once the step exists; progression owns the weight from then on |
| `unit` | no | `reps` | `reps`, or `seconds` for timed holds like planks |
| `bodyweight_factor` | no | `0` | The share of **you** this movement carries, 0 to 1. Read only by [`check`](#does-the-range-fit-the-step) |
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

`min_weights` works the same way and answers a different question: how light a
[deload](progression.md#deloading) may go. It is a fact about your equipment
rather than about the exercise - the smallest bar on the rack, the lightest
pair of dumbbells, the top plate of a stack - so it is usually enough to
declare it per load type and never again. A load type left out has no floor,
which is why a config written before deloads existed still loads.

Override it per exercise where the equipment differs: a machine whose stack
starts at 15 kg, or a barbell lift you would not perform with less than the
20 kg bar even though a 12 kg one exists.

## Does the range fit the step

Everything above works in the units Garmin stores. For most exercises that is
the load: put 60 kg on the bar and you are lifting 60 kg. For one kind of
exercise it is not, and the gap is large enough to make a rep range that reads
perfectly well behave badly.

**You are part of the load.** A standing calf raise moves all of you plus the
stack. Set `bodyweight_factor` to the share of you the movement carries:

| Exercise | `bodyweight_factor` |
| --- | --- |
| Standing calf raise, weighted pull-up, weighted dip | `1.0` |
| Back squat, front squat, lunge, split squat, step-up | `0.85` |
| Push-up | `0.65` |
| Deadlift, row, overhead press - the bar moves, you do not | leave it out |
| Anything you lie or sit down to do | leave it out |

`0.85` is the figure strength coaching conventionally uses for a squat: your
shanks and feet barely rise, so roughly everything above the knee is what the
movement actually lifts, on top of the bar.

It is never guessed. A lat pull-down is categorised `PULL_UP` and carries none
of your bodyweight, so inferring from the category would be wrong on the first
exercise that needed it. The default of none means "the stored weight is the
load", which is what every barbell, cable and dumbbell movement wants.

**Everything else is a matter of what you type into the watch.** An exercise
carrying a pair of dumbbells has no field of its own: enter the weight of the
pair rather than of one of them, and set a `weight_step` and `min_weight` to
match. 2 x 5 kg stepping by 1 kg is 10 kg stepping by 2 - the same ladder,
written so the stored weight is the whole load.

### What it is for

[Rule 3](progression.md#the-five-rules) tops out the range, adds a step, and
resets to `rep_low`. That trade only works if the step is worth more than the
reps just given back. A calf raise programmed 12-20 with a 5 kg step looks like
a 25% jump on a 20 kg stack - ample. With 80 kg of lifter on top it is a 5%
jump, nowhere near enough to pay for dropping 20 reps to 12, and the "weight
increase" leaves the exercise about 12% *easier* than it was. You then spend
six sessions climbing back to ground you already held.

The mirror image is just as wrong. A lateral raise programmed 12-20 at 3 kg
steps by 1 kg, which is a 33% jump - so the reset to 12 reps is about 12%
*harder* than the 20 reps that earned it. That is a wall rather than a
progression, and because [rule 5](progression.md#a-load-has-to-be-earned)
refuses a load you cannot carry for `rep_low`, it becomes a loop: climb the
range, fail the jump, deload, climb it again.

Both are the same number with opposite signs, so `check` reports both, at the
weight the exercise is loaded to today:

```text
Workout A (1631254436)
 ! Weighted Standing Calf Raise: +5 kg on 101 kg is 5.0%, but resetting
   20->12 reps gives back more, so the weight increase is a 12% drop in
   effort (make it 12-14; anything from 12-13 to 12-18 fits, or accept
   the sawtooth)

Workout B (1641921176)
 ! Dumbbell Lateral Raise: +1 kg on 3 kg is 33.3%, but resetting 20->12
   reps gives back less, so the weight increase is a 12% jump in effort
   (make it 12-26; anything from 12-21 to 12-30 fits, or micro-load)
```

| Sign | Meaning | Costs you | Fix |
| --- | --- | --- | --- |
| Positive | Range too **wide** for the step | Sessions spent re-treading ground | Narrow the range |
| Negative | Range too **narrow** for the step | A wall at every weight jump | Widen the range, or micro-load |

Two figures, because the tolerance is a band rather than a line. The first is
the top whose reset breaks even exactly; the rest of the window is every other
top that is still inside the tolerance. Take the balanced one if you have no
opinion, round to something you would rather count to if you do, or note that
your range is already in the window and leave it alone.

### Which end to move

**Only `rep_high` is ever suggested.** The two ends of a range are not the same
kind of number:

- `rep_low` is the only rep count that says how hard the exercise ever gets.
  The set straight after a weight jump - `rep_low` reps at the new weight - is
  the highest relative intensity in the whole cycle. Dropping it to make the
  arithmetic work means training a joint heavier than you chose to, which is a
  large decision to make on a rounding error's behalf.
- `rep_high` decides nothing except when the jump has been earned, and it is a
  function of how strong you are today. The step is a shrinking share of a
  growing load, so the top wants to come down as you progress while the bottom
  does not move at all.

So a range that is too narrow is widened at the top, and one that is too wide
is narrowed at the top. The arithmetic agrees: the break-even top is roughly

```text
rep_high  =  rep_low + (30 + rep_low) x weight_step / effective_load
```

`rep_low` is on both sides, so raising it to narrow a range also widens what
the range needs to be. Moving the bottom fights itself; moving the top does not.

### Choosing rep_low for a new exercise

There is no universally best rep count - hypertrophy is much the same anywhere
from about 5 to 30 reps taken near failure, and the practical limits are
technique at the bottom and Epley's accuracy plus sheer tedium at the top. What
there is, is a tier:

| `rep_low` | Which exercises |
| --- | --- |
| 6 | Heavy axial barbell lifts: squat, deadlift, bench |
| 8 | Every other multi-joint lift: overhead press, rows, pull-downs, incline press, lunges (per side) |
| 10-12 | Single-joint isolation: curls, triceps, leg curl, lateral raise, rear delts |
| 15 | High-rep-tolerant tissue: calves, core |

Pick the tier, put anything plausible in `rep_high`, and let `check` correct the
top on the next run. You never have to look a range up - the only judgement is
the bottom.

Note which way each error drifts. **Too narrow is temporary**: it means the
step is currently too big a share of the load, and it heals itself as you get
stronger, which is why micro-loading is often the better answer than rewriting
the range. **Too wide only worsens**, and a range that has narrowed to a rung
or two is the signal to raise `weight_step` rather than to keep trimming.

Judged per run rather than once, because the answer moves: a step is a
shrinking share of a growing load, so a range that was fine at 20 kg stops
being fine at 40 kg - and one that was a wall at 3 kg stops being one at 12 kg.

Ranges counted per side are read per side. An [alternating
exercise](progression.md#alternating-exercises) is programmed in the watch's
units with `rep_step` set to the number of sides, so a 16-24 range is 8-12 for
the leg doing the work, and that is what the reset is measured against. Taking
it at face value would overstate the cost of every such reset and invent
findings on exercises that are programmed perfectly well.

Some movement is inherent - climbing the range again always gives back part of
what the load gained - so only exercises past **10% either way** are reported.
A well-programmed lift sits within a few percent of zero and stays quiet.

One threshold serves both signs, which is a simplification rather than a claim
that they are equally bad: a wide range only wastes sessions, where a narrow one
stops progress dead. If the narrow side ever proves too quiet, split
`TOLERATED_SHIFT` in two rather than moving it.

No suggestion is offered past 30 reps. A 1 kg step on a 1 kg dumbbell is a 100%
jump that no rep range absorbs, and answering it with `12-47` would be
arithmetic rather than advice - so the finding says to change `weight_step`
instead.

Nothing here changes a target. It is a fact about how you wrote the range, and
the fix is to edit the range.

### Where your bodyweight comes from

Your Garmin weigh-ins, averaged over the last 30 days - so it stays current
without anyone editing a file, and one heavy breakfast does not move a
threshold. Nothing is written to Garmin, and the request is only made when some
exercise actually declares a `bodyweight_factor`.

Set `settings.bodyweight` to state it yourself instead. That wins outright, and
is the answer for an account with no weigh-ins. Without either, exercises that
needed it say they were skipped rather than being silently passed.

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
- a `bodyweight_factor` outside 0 to 1
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

## Reusing what is on disk

`activity_caching` turns `dump_dir` from a place dumps land into the copy this
tool prefers:

```yaml
settings:
  garmin:
    dump_dir: ~/git/repwise/dumps   # absolute: see below
    activity_caching: true
```

**Name an absolute path.** `dump_dir` is relative to the directory you run
repwise in, so the default `.` gives you a different cache every time you run
from somewhere else - each one cold, each one downloading the search limit
again and leaving its own pile of dumps behind. `~` is expanded, so
`~/git/repwise/dumps` is enough.

With it on, [`update`](commands.md#update) fetches the list of recent
activities as it always did, downloads every strength session in that list it
does not already hold, and works everything out from disk. The first run pays
for the search limit. Every run after it asks Garmin for the list and nothing
else, because the sessions it reads back are already there.

[`fetch activities`](commands.md#fetch-activities) follows the same rule and
downloads only what is missing.

**A session is three files, and all three have to be there.** `update` reads
the sets and the executed workout and never the summary, so the index that
records what has been filed - `activity-index.json`, beside the payloads -
tracks each one separately. Delete any of them and it is downloaded again;
delete the index and everything is.

`-v` says what the cache did with every payload, which is how to tell a cache
that is working from one that quietly never hits:

```text
DEBUG   repwise.dumps: Cache hit for sets-23896913928.json
DEBUG   repwise.dumps: Cache miss for executed-23896913928.json: that payload
                       has never been asked for
DEBUG   repwise.dumps: Filing executed-23896913928.json
```

A miss says which of the three reasons it was: the session has never been
filed, that one payload of it has not, or the file has been deleted since.

### When a copy stops being true

A session that is over does not change. What changes is what your watch got
wrong and you corrected in Connect afterwards: a rep it missed, a set it
invented.

Garmin reports `totalSets`, `totalReps` and `totalVolume` for every strength
activity in the list of recent ones, and that list is fetched anyway - so an
edit is detectable without spending a request on it. The index records those
three numbers for each session it files, and drops the session as soon as
Garmin's copy of them stops matching:

```text
1 cached session(s) no longer match Garmin and will be downloaded again.
```

Under `-v` that line is preceded by which session and which number moved:

```text
DEBUG   repwise.dumps: Cache stale for 23896913928: totalReps 240 -> 241
```

The session is then downloaded again, and your correction reaches the next
target. This is why the setting is safe to leave on. What it cannot see is an
edit that moves none of the three - renaming a session, or swapping which
exercise a set was filed under without changing the reps. For those,
[`repwise fetch activities --force`](commands.md#fetch-activities) downloads
everything again regardless.

A session older than `activity_search_limit` is in no list, so nothing
contradicts it and the copy on disk stands. That is the point of keeping them:
history that has scrolled out of Garmin's window is still yours to read.

## Finding your exercise identifiers

`garmin_name` and `garmin_category` must match what Garmin stores. Rather than
guess, dump a real session and read them off:

```bash
repwise fetch activities   # writes activity-*.json, sets-*.json, executed-*.json
```

Each executable step in `executed-*.json` carries `exerciseName` and
`category`; copy those into `workouts.yaml`. The `sets-*.json` beside it says
what your watch actually detected, which is worth comparing when a name does
not match. [`repwise check`](commands.md#check) finds any that do not match,
and is worth running after editing these by hand.

For an exercise no workout of yours holds yet, the authority is [Garmin's
exercise catalog](garmin-api.md#the-exercise-catalog), which `check` downloads
for itself and which `repwise fetch exercises` refreshes. Garmin validates
`garmin_name` and `garmin_category` against each other, so both have to be
right - and `check` names the pair it should be:

```text
   !! Barbell Deadlift: BARBELL_DEADLIFT is filed under DEADLIFT, not SQUAT.
      Garmin checks the pair, so set garmin_category: DEADLIFT
```

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
