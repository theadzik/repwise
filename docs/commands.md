# Commands

Full reference. Every command accepts the global `--config` option:

```bash
workout --config /path/to/other.yaml update
```

- [update](#update) - advance targets from the last session
- [list](#list) - see your Garmin workouts
- [import](#import) - build a config from Garmin
- [check](#check) - find drift between config and Garmin
- [fetch](#fetch) - download raw payloads
- [Exit codes](#exit-codes)

## update

The main command. Reads your latest logged session and advances the targets in
the matching Garmin workout.

```bash
workout update                    # dry run, shows changes
workout update --apply            # write to Garmin
workout update --apply --push     # and send them to your watch
workout update --dump             # save raw JSON, change nothing
workout update --activity 1234    # use a specific activity
```

| Flag | Effect |
|---|---|
| *(none)* | Dry run. Prints the plan and writes nothing |
| `--apply` | Write the new targets to Garmin Connect |
| `--push` | Queue the written workouts for your devices. Requires `--apply` |
| `--activity ID` | Use this activity instead of the most recent match |
| `--dump` | Also save the raw activity and workout JSON |

**Dry run is the default.** Nothing is sent to Garmin without `--apply`.

### Choosing the activity

Without `--activity`, the most recent activity whose name starts with one of a
workout's `activity_prefixes` is used, searching back
`settings.garmin.activity_search_limit` activities. Prefixes are matched
case-insensitively, so `["workout a", "trening a"]` catches either spelling.

Older sessions need `--activity` with the id, which `--dump` filenames and
Garmin Connect URLs both contain.

### Reading the output

```text
Activity: Workout B (1234567890)
Updating: Workout B -> workout 111111111

* Barbell Deadlift          10 x 60 kg  ->  6 x 65 kg    (hit 10 on every set, +5 kg and reset to 6)
  Sit-up                       11 reps  ->  11 reps      (missed target (10/11 on worst set), repeat)
  ! Standing Calf Raise: not found in the activity, skipped

Also in Workout A (workout 222222222):
* Standing Calf Raise         12 x 0 kg  ->  12 x 20 kg  (synced from Workout B)

Dry run: 3 step(s) would change. Re-run with --apply.
```

| Marker | Meaning |
|---|---|
| `*` | This step would change |
| *(space)* | Unchanged, with the reason why |
| `!` | Warning: the exercise was skipped, and why |

Targets print in the unit the exercise is measured in - `6 x 65 kg` for loaded
work, `11 reps` for bodyweight, `47 s` for timed holds.

An "Also in ..." section appears when a target that moved also exists in another
workout. See [shared exercises](progression.md#shared-exercises).

### Sending to the watch

Editing a workout in Garmin Connect **does not reach the watch on its own**. A
plain device sync is not enough: the watch only collects a new copy when a
message is waiting for it, which is what the Connect app's "Send to Device"
button queues.

`--push` does that for every workout the run wrote:

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

The endpoint behind this is undocumented; see
[Garmin's API](garmin-api.md#device-messages).

## list

```bash
workout list          # your strength workouts and their ids
workout list --all    # every workout, whatever the sport
```

Use it to find the `garmin_workout_id` values for your config, or to confirm
what Garmin actually has.

## import

Turns existing Garmin workouts into a config file.

```bash
workout import                    # print to stdout
workout import -o workouts.yaml   # write to a file
workout import --name "Workout A" # only workouts matching a substring
workout import --id 111111111     # only this workout
```

| Flag | Effect |
|---|---|
| `-o`, `--output PATH` | Write to a file instead of stdout |
| `--force` | Overwrite an existing file |
| `--name TEXT` | Only workouts whose name contains this, case-insensitively |
| `--id ID` | Only this workout id. Mutually exclusive with `--name` |

**It never modifies a config in place.** Without `--force` it refuses to
overwrite, so your comments and tuned values are safe.

Garmin knows less than this tool needs, so three fields are inferred and want
checking:

| Field | How |
|---|---|
| `rep_low` | Garmin's current target becomes the bottom of the range |
| `rep_high` | A suggestion: `rep_low` plus a few. **Check it** |
| `load` | Guessed from the exercise name (`BARBELL_*`, `DUMBBELL_*`, `CABLE_*`), else `machine` if loaded and `bodyweight` if not |

`rep_step`, `weight_step` and `video` have no Garmin equivalent at all and are
left to you. Everything else - `sets`, `rest`, `unit`, `garmin_name`,
`garmin_category`, `garmin_workout_id` - is read straight from the payload.

Garmin's API has no server-side name search, so `--name` filters locally.

## check

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

## fetch

Downloads workout definitions as JSON. Mostly a connectivity check and a way to
inspect Garmin's schema by hand.

```bash
workout fetch                   # every workout in workouts.yaml
workout fetch 111111111         # a specific workout
```

Files land in `settings.garmin.dump_dir`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Nothing usable in the activity, or a fetch failed |
| `2` | Rate limited by Garmin |
| `3` | Bad configuration, or `--push` without `--apply` |

`check` also exits non-zero when it finds real drift, which is what makes it
usable from a scheduler.
