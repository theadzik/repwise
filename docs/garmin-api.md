# Garmin's API

Notes on the endpoints and payloads this tool depends on, verified against real
responses. Garmin does not document any of this, so treat it as observed
behaviour rather than a contract.

Everything here is implemented in `src/workout/garmin/payloads.py`, which is the
only module that knows Garmin's schema - except the exercise catalog below,
which is a static file rather than an API and lives in
`src/workout/garmin/catalog.py`. If Garmin changes something, those are where
the fix goes.

- [Weight units](#weight-units)
- [Fields relied on](#fields-relied-on)
- [Sets are repeat groups](#sets-are-repeat-groups)
- [The workout an activity was performed against](#the-workout-an-activity-was-performed-against)
- [Creating a workout](#creating-a-workout)
- [Step order is a field, not a position](#step-order-is-a-field-not-a-position)
- [Names drift between payloads](#names-drift-between-payloads)
- [Device messages](#device-messages)
- [The workout list endpoint](#the-workout-list-endpoint)
- [The exercise catalog](#the-exercise-catalog)

## Weight units

**The two payloads use different units.** This is the easiest thing to get
wrong:

| Payload | Field | Unit |
| --- | --- | --- |
| Exercise set (activity) | `weight` | grams - `20000.0` means 20 kg |
| Workout step | `weightValue` | whatever `weightUnit` says, normally kilograms - `30.0` means 30 kg |

`step_weight_factor()` reads `weightUnit.factor` (grams per unit, 1000 for
kilograms) so both directions go through `value * factor / 1000`.

Treating `weightValue` as grams makes every stored target read as a 0.03 kg-style
fraction, which is the symptom to look for.

A step with no load carries `weightValue: null` **and** `weightUnit: null`. Note
that the key is present, so a `setdefault` will not replace it - writing a
weight onto such a step has to set the unit explicitly.

## Fields relied on

| Payload | Field | Used for |
| --- | --- | --- |
| Workout step | `exerciseName` | Matching to an exercise |
| Workout step | `category` | Fallback match, e.g. `SQUAT` |
| Workout step | `endCondition.conditionTypeKey` | `reps` normally, `time` for timed holds |
| Workout step | `endConditionValue` | Current and new rep or second target |
| Workout step | `weightValue`, `weightUnit` | Current and new load |
| Workout step | `description` | The step's notes, see below |
| Rest step | `endCondition`, `endConditionValue` | The rest between sets, when it is a fixed time |
| Exercise set | `setType == "ACTIVE"` | Skipping rest sets |
| Exercise set | `repetitionCount` | Reps performed |
| Exercise set | `duration` | Seconds held, for timed exercises |
| Exercise set | `weight` | Load used, in grams |
| Exercise set | `exercises[0].name` | Which exercise the set belongs to |
| Exercise set | `exercises[0].category` | Fallback when the name differs or is null |
| Executed step | `durationType`, `durationValue` | What a past session was asked for |
| Executed step | `targetValue` | How many sets, on a repeat step |
| Device message | `messageUrl`, `messageType`, `metaDataId` | Queueing a workout for the watch |

## Notes are called `description`

The per-step notes field - what Garmin Connect labels **Notes** on a step, and
what the watch reads as `WorkoutStepInfo.notes` - is `description` in the JSON.
There is no field named `notes`. One sits on every `ExecutableStepDTO`,
including rest steps, and there is a second, unrelated `description` at the top
level of the workout, which is the workout's own note rather than a step's.

It is `null` until something writes it, and `""` once Connect has opened and
cleared it, so absent, null and empty all have to read as "no note".

Verified by round-trip against a real account rather than by inference: writing
a value, saving with `update_workout`, and fetching the workout back returns it
unchanged.

```text
workoutSegments[].workoutSteps[]        <- RepeatGroupDTO
  └── workoutSteps[]                    <- ExecutableStepDTO
        description: "6-10 reps | +5 kg"
```

Because it is a single free-text field with no structure, writing to it means
overwriting whatever was there. `GENERATED_NOTE` in `payloads.py` matches the
shape this tool renders, which is what lets it tell its own note from one you
typed and leave the latter alone.

## Sets are repeat groups

A workout holds **one step per exercise, not one per set**. Sets are modelled as
a `RepeatGroupDTO` with `numberOfIterations` wrapping one executable step plus a
rest step:

```text
RepeatGroupDTO (numberOfIterations: 4)
├── ExecutableStepDTO   exerciseName, endCondition: reps, weightValue
└── ExecutableStepDTO   stepType: rest, endCondition: lap.button
```

`iter_exercise_blocks()` walks into those groups and yields one `ExerciseBlock`
per exercise: the step itself, the group's `numberOfIterations`, and the rest
step beside it. Rest steps end on `time` or `lap.button` and so return no
target of their own.

The rest step is what `rest` in `workouts.yaml` reads and writes, through
`endConditionValue` in seconds. Only a `time` rest holds an interval - a
`lap.button` one carries a duration Garmin ignores, so it reads as no rest at
all rather than as the number stored beside it.

Note which rest that is. The step **inside** the repeat group is the rest
between sets; Connect also emits a `lap.button` rest **after** each group,
which is the pause between exercises.

Whether the final set gets that rest at all is a third thing, held on the group
rather than on either step: `skipLastRestStep`. Connect sets it per group, and
Garmin returns it as `true`, `false` or `null` - the last two both meaning the
rest is performed. A workout can therefore hold one exercise that skips and
seven that do not, with nothing in the steps to show for it, which is why
`update` writes it back to `false` everywhere.

### One exercise can need two groups

A group repeats one step identically, so a target that asks more of the leading
sets than of the rest - which is what [progression](progression.md#coming-back-from-a-stall)
writes on the way back from a stall - needs **two adjacent groups naming the
same exercise**, the harder one first.

Verified against a real account: Garmin accepts them, keeps them apart with
their own targets and iteration counts, and returns them exactly as sent, so a
second run finds nothing to do. Splitting one group into two and merging them
back are both ordinary PUTs.

No gap step goes between the two halves. The first group's own rest already
covers the pause between its last set and the next one, and a `lap.button` rest
there would be a second pause the config never asked for.

`iter_exercise_blocks()` merges the pair back into one `ExerciseBlock` whose
`sets` is the two counts added together, so nothing above the adapter has to
know: the planner still matches one spec to one block, and `check` cannot see
the same name twice and call it ambiguous.

### Step ids are reissued on every save

`stepId` is **not stable across a PUT**. Saving a workout returns fresh ids for
every step, including ones that were sent back unchanged - verified by walking
one workout through three saves and watching an untouched step's id change each
time. What survives is the step's *content*: its target, its notes, its
position. Nothing should key on `stepId` between runs.

## The workout an activity was performed against

```text
GET /activity-service/activity/{activityId}/workouts
```

Returns the workout **as the watch actually ran it**, kept beside the activity.
This is the only record of what a past session was asked for: the definition in
`/workout-service` holds the target for the *next* session, because `update`
rewrote it once that one was logged. An array, empty for an activity not
performed against a workout at all.

It is FIT's shape rather than a workout definition's - flat, with no nesting:

| Field | Meaning |
| --- | --- |
| `stepIndex` | Position, and what an activity set's `wktStepIndex` refers to |
| `intensity` | `ACTIVE` for a working step, `REST` for a rest, null for a repeat |
| `durationType` | `REPS`, `TIME`, `OPEN`, or `REPEAT_UNTIL_STEPS_CMPLT` |
| `durationValue` | The rep or second target - or, on a repeat, the step to go back to |
| `targetValue` | On a repeat, how many times to run it |
| `exerciseName`, `exerciseCategory` | Matched to a spec the usual way |
| `notes` | The step's note, as it was at the time |

**Sets are a repeat step placed *after* the run it repeats**, saying which step
to jump back to and how many times, rather than a group wrapping them. A
four-set squat is the exercise step, its rest, then a repeat with
`durationValue: 0` and `targetValue: 4`.

**`exerciseWeightValue` is always null**, with `weightDisplayUnit` set beside it
regardless. The weight is not missing from the record, only from this JSON: the
activity's own FIT file carries it in `workout_step.exercise_weight` (scaled by
100), as does the FIT the watch downloads. Nothing this tool sends is missing -
verified by decoding both files - so there is no field to add that would make it
appear. Whether the load changed is read off what was actually lifted instead,
which the exercise sets already give.

`wktStepIndex` on an exercise set links it back to a step here, but it is **not
reliable**: sets come back with it null - a whole timed exercise did, in one
real activity - so matching stays by name and category like everywhere else.

## Creating a workout

`POST /workout-service/workout` creates one and returns it with a server-issued
`workoutId`. That is `upload_workout()` in `garminconnect`; the PUT that
replaces an existing workout is `update_workout()`.

**Garmin accepts far less than Connect sends.** Verified by building a
three-exercise workout by hand, posting it, and reading it back: what comes
back has exactly the key set a Connect-built workout has, with everything
omitted filled in as `null`. The minimum that produced a correct workout:

```json
{
  "workoutName": "...",
  "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
  "workoutSegments": [{
    "segmentOrder": 1,
    "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
    "workoutSteps": [ ... ]
  }]
}
```

Per step, only these are needed - `displayOrder` inside the type objects,
`preferredEndConditionUnit`, `strokeType`, `equipmentType` and the secondary
target fields can all be left out:

| Step | Required |
| --- | --- |
| Repeat group | `type: RepeatGroupDTO`, `stepOrder`, `stepType` `repeat` (id 6), `childStepId`, `numberOfIterations`, `endCondition` `iterations` (id 7) with the set count as `endConditionValue`, `smartRepeat: false`, `workoutSteps` |
| Exercise | `type: ExecutableStepDTO`, `stepOrder`, `stepType` `interval` (id 3), `childStepId`, `endCondition` `reps` (id 10) or `time` (id 2) with `endConditionValue`, `category`, `exerciseName`, and `weightValue` + `weightUnit` when loaded |
| Rest | as above but `stepType` `rest` (id 5), and `endCondition` `time` (id 2) with seconds, or `lap.button` (id 1) with `endConditionValue: null` |

Two differences from a Connect-built workout survive the round trip, neither of
which stopped the workout working:

- `targetType` comes back `null` where Connect sets `no.target` (id 1), and
  `targetValueTwo` `null` where Connect sets `0.0`. Cheap to send, so send them.
- `estimatedDurationInSecs` is not computed for a posted workout. Connect shows
  an estimate for its own; ours has none until Connect next saves it.

`childStepId` on a group's children is corrected server-side to the parent
group's value, whatever is sent. Send it correctly anyway, so that a payload
built offline equals the one that comes back and a second run has nothing to do.

### The PUT takes structural changes too

`update_workout()` replaces the whole workout, and it accepts a step list that
has been **reordered, added to and cut down** in the same request - not only
one whose values changed. Verified by taking a three-exercise workout, dropping
the first exercise, swapping the other two, appending a group built from
scratch with no `stepId`, renumbering, and putting it back:

- The order came back as sent, and the dropped exercise was gone.
- The appended group was created and given ids, `stepId` not being needed to
  add a step.
- The **kept steps held their targets and notes**, which is what makes it safe
  to rearrange a workout without resetting the progression stored in it.
- What was sent equalled what came back, field for field, so a second run finds
  nothing to do.

That last point only holds because the numbering sent was the numbering Garmin
would have chosen. Anything else round-trips into a difference, and every run
would write again.

## Step order is a field, not a position

**`stepOrder` decides the sequence; the position in `workoutSteps` does not.**
Verified by posting three exercises in one array order with `stepOrder` values
saying the opposite: the workout came back in `stepOrder`'s order, renumbered
to a contiguous 1..N with `childStepId` reassigned 1..N by group to match.

So reordering exercises means **renumbering**, not rearranging a list. The
numbering Garmin settles on, and therefore the one to build:

| Field | Rule |
| --- | --- |
| `stepOrder` | Flat 1..N depth-first across the segment, counting groups and their children alike |
| `childStepId` | 1 for the first repeat group, 2 for the second, and so on. A group's children carry its value. `null` on steps outside a group |

## Names drift between payloads

Garmin auto-detects the exercise while you lift, so what it logs need not match
what the workout programs, and can be null entirely:

| Programmed in the workout | Logged in the activity |
| --- | --- |
| `STANDING_ALTERNATING_DUMBBELL_CURLS` | `SEATED_DUMBBELL_BICEPS_CURL` |
| `WEIGHTED_LEG_CURL` | `LEG_CURL` |
| `CABLE_OVERHEAD_TRICEPS_EXTENSION` | `null` |

The `category` survived all three cases, which is why `garmin_category` exists
in the config and why matching falls back to it.

Categories are also not always the obvious ones - a lat pulldown is filed under
`PULL_UP`, a face pull under `ROW`.

## Device messages

Editing a workout does **not** reach the watch. The device only collects a new
copy when a message is queued for it, which is what Connect's "Send to Device"
button does.

```text
POST /device-service/devicemessage/messages
GET  /device-service/devicemessage/messages
```

The POST body is a **JSON array**. A bare object returns HTTP 500:

```json
[
  {
    "deviceId": 1234567890,
    "messageUrl": "workout-service/workout/FIT/111111111",
    "messageType": "workouts",
    "messageName": "Workout B",
    "groupName": null,
    "priority": 1,
    "fileType": "FIT",
    "metaDataId": 111111111
  }
]
```

`deviceId` comes from `get_devices()` and is **required per message** - there is
no broadcast form. `metaDataId` and the id in `messageUrl` are both the workout
id. `priority` is only a hint; the server rewrites it.

**garminconnect 0.3.7 does this for you.** `push_workout_to_device(workout_id,
device_id)` builds exactly the payload above, so this tool no longer constructs
it. Two details of that method are worth knowing:

- Omitting `device_id` sends to the **last-used device only**, which is what
  `--push` relies on. Targeting a particular device, or several, means reading
  ids from `get_devices()` and calling the method once per device.
- It looks the workout name up itself with `get_workout_by_id`, so the message
  is labelled with Garmin's name rather than your config's `key`, at the cost of
  one extra request per push.

The same release added `update_workout(workout_id, workout_json)`, which is the
PUT this tool uses to save a workout. It forces `workoutId` in the body to match
the id in the URL, so the workout keeps its identity and any calendar schedules
pointing at it stay valid.

The GET returns `numOfMessages` and a `messages` list, and is the way to confirm
something was queued. The queue drains when the watch syncs. garminconnect has
no getter for it, so `GarminSession.pending_messages()` makes that call by hand,
taking the URL from the library rather than repeating it. `update --apply
--push -v` reads it back after queueing, so the confirmation is available
without writing code.

Things that do not work, for the record:

- `/workout-service/workout/{id}/sendToDevice` - looks plausible but
  `/workout-service/workout/{id}/<anything>` returns `200 []`, including
  `/bananas`. It is a catch-all, not an endpoint.
- POST or PUT with a bare object instead of an array - HTTP 500.
- `PUT /device-service/devicemessage/messages` - HTTP 405.

## The workout list endpoint

```text
GET /workout-service/workouts?start=0&limit=200&sportTypeKey=strength_training
```

`sportTypeKey` filters server-side and `orderBy` sorts, but **there is no name
search**: `searchTerm`, `name`, `q` and friends are silently ignored. Name
filtering has to happen locally, which is what `workout list --name` does.

Garmin caps a response at the requested size rather than reporting a total, so a
full page means there may be more and pagination has to keep asking.

## The exercise catalog

Every exercise Garmin knows is published as a static file, with no account and
no token involved:

```text
GET https://connect.garmin.com/web-data/exercises/Exercises.json
```

About 200 KB, and at the time of writing 1510 exercises across 47 categories.
It is what Garmin's own workout editor is built from, which makes it the one
authority on whether a `garmin_name` names something real - a question `check`
could otherwise only ask of exercises a workout already holds.

```json
{
  "categories": {
    "DEADLIFT": {
      "exercises": {
        "BARBELL_DEADLIFT": {
          "primaryMuscles": ["HAMSTRINGS", "LOWER_BACK"],
          "secondaryMuscles": ["LATS", "TRAPS", "FOREARM", "QUADS", "GLUTES", "ADDUCTORS"]
        }
      }
    }
  }
}
```

The two keys are exactly `garmin_category` and `garmin_name` from
`workouts.yaml`, and **Garmin validates the pair**: a real exercise filed under
the wrong category is rejected like an invented one. That is why `check`
reports the pair rather than the name alone.

**Send a User-Agent.** Garmin answers `403` to urllib's default
`Python-urllib/x.y`. Any other string works, including an honest one naming
this tool - the default is refused for being the default, not for being
unusual. This costs a false negative on every exercise at once if you miss it,
which reads convincingly like the catalog having changed shape.

`primaryMuscles` and `secondaryMuscles` are what drives the muscle map in
Connect. Nothing here reads them yet; the cached copy keeps the whole payload
rather than the two fields `check` uses, so they are already on disk for
whatever wants them next.

### The per-exercise files are not a substitute

There is also a per-exercise endpoint carrying descriptions, tips and videos:

```text
GET https://connect.garmin.com/web-data/exercises/en-US/DEADLIFT/BARBELL_DEADLIFT.json
```

**It is incomplete.** Real exercises 404 there while resolving fine in
`Exercises.json` - `WEIGHTED_LEG_CURL`, `DUMBBELL_LATERAL_RAISE` and
`WEIGHTED_STANDING_CALF_RAISE` among them. Validating against it would report
exercises as invented that Garmin holds perfectly well, so the master file is
the one to use. Both paths are case-sensitive and upper-case.
