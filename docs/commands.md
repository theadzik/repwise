# Commands

Full reference. Every command accepts `--config` and `--verbose`, either
before or after the command name:

```bash
workout --config /path/to/other.yaml update
workout update --verbose
```

| Option | Effect |
| --- | --- |
| `--config PATH` | Read this file instead of searching for `workouts.yaml`. See [where the file lives](configuration.md#where-the-file-lives) |
| `-v`, `--verbose` | Also show debug output, prefixed with its level and source |
| `--version` | Print the version and exit. Worth quoting in a bug report |

Results go to stdout, so they pipe and redirect; warnings and errors go to
stderr, so they stay visible when they do.

- [update](#update) - advance targets from the last session
- [list](#list) - see your Garmin workouts
- [import](#import) - build a config from Garmin
- [check](#check) - find drift between config and Garmin
- [fetch](#fetch) - download raw payloads
- [Exit codes](#exit-codes)

## update

The main command. Reads the latest logged session of every workout in your
config and advances the targets in each.

```bash
workout update                    # dry run, shows changes
workout update --apply            # write to Garmin
workout update --apply --push     # and send them to your watch
workout update --dump             # save raw JSON, change nothing
workout update --activity 1234    # use a specific activity
```

| Flag | Effect |
| --- | --- |
| *(none)* | Dry run. Prints the plan and writes nothing |
| `--apply` | Write the new targets to Garmin Connect |
| `--push` | Queue the written workouts for your devices. Requires `--apply` |
| `--activity ID` | Update from this one activity instead of scanning |
| `--dump` | Also save the raw activity and workout JSON |

**Dry run is the default.** Nothing is sent to Garmin without `--apply`.

### Choosing the activities

Without `--activity`, each workout gets its own most recent activity: the
latest one whose name starts with any of that workout's `activity_prefixes`,
searching back `settings.garmin.activity_search_limit` activities. Prefixes are
matched case-insensitively, so `["workout a", "trening a"]` catches either
spelling.

**Train A, then B, then run once and both advance.** You do not have to run the
tool between sessions. A workout with no matching activity in that window is
simply left out, and only a run that matches nothing at all is an error.

Sessions are replayed oldest first, so the result is the same as having run the
tool after each of them. That also settles [shared
exercises](progression.md#shared-exercises): where two sessions both moved one,
the more recent has the last word.

Only the *latest* activity per workout is read. If you trained the same workout
twice since the last run, the earlier one is skipped - replay it with
`--activity` and its id, which `--dump` filenames and Garmin Connect URLs both
contain. Do that before the later session's run, or apply them in order.
Re-processing an activity is safe: the second pass reads its own applied target
and reports "missed target ... repeat" rather than advancing again.

### Reading the output

```text
Activity: Workout B (1234567890)
Updating: Workout B -> workout 111111111

* Barbell Deadlift          10 x 60 kg  ->  6 x 65 kg    (hit 10 on every set, +5 kg and reset to 6)
  Sit-up                       11 reps  ->  11 reps      (missed target (10/11 on worst set), repeat)
* Standing Calf Raise         12 x 0 kg  ->  12 x 20 kg  (add 1 rep (12 -> 13))

Also in Workout A (workout 222222222):
* Standing Calf Raise         12 x 0 kg  ->  12 x 20 kg  (synced from Workout B)

Activity: Workout A (1234567891)
Updating: Workout A -> workout 222222222

* Barbell Back Squat         7 x 30 kg  ->  8 x 30 kg    (add 1 rep (7 -> 8))
  ! Plank: not found in the activity, skipped

Dry run: 4 step(s) would change. Re-run with --apply.
```

One block per workout that had a session, oldest first.

| Marker | Meaning |
| --- | --- |
| `*` | This step would change |
| *(space)* | Unchanged, with the reason why |
| `!` | Warning: the exercise was skipped, and why |

Targets print in the unit the exercise is measured in - `6 x 65 kg` for loaded
work, `11 reps` for bodyweight, `47 s` for timed holds.

The closing line also counts notes when any were rewritten:

```text
Dry run: 8 step(s) would change, 17 note(s) would be refreshed. Re-run with --apply.
```

See [step notes](#step-notes). Which exercises they were is behind `-v`, since
notes only move when you edit `workouts.yaml`.

An "Also in ..." section appears when a target that moved also exists in another
workout. See [shared exercises](progression.md#shared-exercises).

### Step notes

Every exercise step carries a one-line note saying how it is programmed, so the
watch can show what you are working towards while the target only says what to
do today:

```text
6-10 reps | +5 kg          a barbell lift stepping 2.5 kg at the top
16-24 reps by 2 | +1 kg    a per-side exercise, both sides advancing together
10-25 reps | bodyweight    nothing to add, so the range is the whole ladder
30-60 s | bodyweight       a timed hold
```

These are refreshed from `workouts.yaml` on every run, so editing a rep range
or a `weight_step` updates them. That is a reason to write a workout in its own
right: a config edit moves no target, and without it the notes would go stale
until your next session happened to earn something.

**A note you wrote yourself is never overwritten.** If a step's notes hold
anything that is not in the shape above - a coaching cue, say - the tool
reports it and leaves it alone:

```text
  ! Barbell Back Squat: has its own note, left alone (wanted '6-10 reps | +5 kg')
```

Clear the field in Garmin Connect if you would rather have the generated note
back.

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

Queued 1 send(s) to your last-used device.
Sync your watch to pick up the new targets.
```

It requires `--apply` - without it nothing has been written, so there is
nothing to send - and refuses with exit code 3 if used alone.

The message goes to **the device you last used**, which is the right one if you
train with a single watch. Sending to a specific device, or to several, is not
currently exposed; see [Garmin's API](garmin-api.md#device-messages) for what
that would take.

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
| --- | --- |
| `-o`, `--output PATH` | Write to a file instead of stdout |
| `--force` | Overwrite an existing file |
| `--name TEXT` | Only workouts whose name contains this, case-insensitively |
| `--id ID` | Only this workout id. Mutually exclusive with `--name` |

**It never modifies a config in place.** Without `--force` it refuses to
overwrite, so your comments and tuned values are safe.

Garmin knows less than this tool needs, so three fields are inferred and want
checking:

| Field | How |
| --- | --- |
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
disagree: an exercise renamed in the Garmin app, a set count or rest time
changed, an exercise present in one but not the other.

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
| --- | --- |
| `0` | Success |
| `1` | Nothing usable in the activity, or a fetch failed |
| `2` | Rate limited by Garmin |
| `3` | Bad configuration, or `--push` without `--apply` |

`check` also exits non-zero when it finds real drift, which is what makes it
usable from a scheduler.
