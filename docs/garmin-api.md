# Garmin's API

Notes on the endpoints and payloads this tool depends on, verified against real
responses. Garmin does not document any of this, so treat it as observed
behaviour rather than a contract.

Everything here is implemented in `src/workout/garmin/payloads.py`, which is the
only module that knows Garmin's schema. If Garmin changes something, that file
is where the fix goes.

- [Weight units](#weight-units)
- [Fields relied on](#fields-relied-on)
- [Sets are repeat groups](#sets-are-repeat-groups)
- [Names drift between payloads](#names-drift-between-payloads)
- [Device messages](#device-messages)
- [The workout list endpoint](#the-workout-list-endpoint)

## Weight units

**The two payloads use different units.** This is the easiest thing to get
wrong:

| Payload | Field | Unit |
|---|---|---|
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
|---|---|---|
| Workout step | `exerciseName` | Matching to an exercise |
| Workout step | `category` | Fallback match, e.g. `SQUAT` |
| Workout step | `endCondition.conditionTypeKey` | `reps` normally, `time` for timed holds |
| Workout step | `endConditionValue` | Current and new rep or second target |
| Workout step | `weightValue`, `weightUnit` | Current and new load |
| Exercise set | `setType == "ACTIVE"` | Skipping rest sets |
| Exercise set | `repetitionCount` | Reps performed |
| Exercise set | `duration` | Seconds held, for timed exercises |
| Exercise set | `weight` | Load used, in grams |
| Exercise set | `exercises[0].name` | Which exercise the set belongs to |
| Exercise set | `exercises[0].category` | Fallback when the name differs or is null |
| Device message | `messageUrl`, `messageType`, `metaDataId` | Queueing a workout for the watch |

## Sets are repeat groups

A workout holds **one step per exercise, not one per set**. Sets are modelled as
a `RepeatGroupDTO` with `numberOfIterations` wrapping one executable step plus a
rest step:

```text
RepeatGroupDTO (numberOfIterations: 4)
├── ExecutableStepDTO   exerciseName, endCondition: reps, weightValue
└── ExecutableStepDTO   stepType: rest, endCondition: lap.button
```

`iter_workout_steps()` walks into those groups. Rest steps end on `time` or
`lap.button` and so return no target.

## Names drift between payloads

Garmin auto-detects the exercise while you lift, so what it logs need not match
what the workout programs, and can be null entirely:

| Programmed in the workout | Logged in the activity |
|---|---|
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

- Omitting `device_id` sends to the **last-used device only**. Because `--push`
  addresses every device the config selects, it always passes the id explicitly
  and calls the method once per device.
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
taking the URL from the library rather than repeating it.

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
