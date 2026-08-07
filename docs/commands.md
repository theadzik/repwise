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

The main command, and two jobs in one. It brings every workout in your config
into line with what the file says, and it advances the targets of the ones you
have trained.

### What the config drives

`workouts.yaml` decides what a workout **is**; Garmin keeps track of where each
exercise has **got to**. Everything in the first column is applied on every
run, trained or not:

| From the config | From your sessions |
| --- | --- |
| Which workouts exist, creating any Garmin lacks | The target: reps and weight |
| Which exercises each holds, and in what order | |
| `sets`, `rest`, and `rest_between_exercises` | |
| The note on each step | |

That split is why an exercise Garmin already holds is **moved rather than
rebuilt** when you reorder it: the target lives in the step and nowhere else,
so a rebuilt step would quietly restart your progression.

A config edit therefore reaches Garmin on the next run, without waiting for
that workout to come round again. A run that matches no activity at all still
does the first column, and says so.

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
| `--apply` | Write to Garmin Connect: new targets, and any workout the config creates or reshapes |
| `--push` | Queue the written workouts for your devices. Requires `--apply` |
| `--activity ID` | Update from this one activity instead of scanning |
| `--dump` | Also save the raw activity and workout JSON |

**Dry run is the default.** Nothing is sent to Garmin without `--apply`, and
nothing is written back to `workouts.yaml` either.

### Choosing the activities

Without `--activity`, each workout gets its own most recent activity: the
latest one whose name starts with any of that workout's `activity_prefixes`,
searching back `settings.garmin.activity_search_limit` activities. Prefixes are
matched case-insensitively, so `["workout a", "trening a"]` catches either
spelling.

**Train A, then B, then run once and both advance.** You do not have to run the
tool between sessions. A workout with no matching activity in that window keeps
whatever targets it has, and is still brought in line with the config.

A run that matches no activity at all is only an error when there was nothing
to do anyway. If the config asks for anything, the run says the activity was
missing and gets on with the rest:

```text
No recent activity matching ['workout a', 'workout b']. Pass --activity <id> to
choose one explicitly. Shaping the workouts from the config regardless.
```

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

One block per workout that had a session, oldest first, each headed by the
activity it learned from. A workout the config changed but no session touched
gets a block of its own, headed `Shaping:` or `Creating:` instead.

| Marker | Meaning |
| --- | --- |
| `*` | This step would change |
| *(space)* | Unchanged, with the reason why |
| `!` | Warning: the exercise was skipped, and why |
| `+` `-` `~` | An exercise added, removed, or moved. See [ordering](#ordering-adding-and-removing) |

Targets print in the unit the exercise is measured in - `6 x 65 kg` for loaded
work, `11 reps` for bodyweight, `47 s` for timed holds.

The closing line counts everything else the run would touch:

```text
Dry run: 8 step(s) would change, 2 exercise(s) would be added, removed or moved, 1 set count(s) would change, 1 rest time(s) would change, 1 step(s) would stop skipping their last rest, 17 note(s) would be refreshed. Re-run with --apply.
```

Each clause appears only when it applies. See [ordering, adding and
removing](#ordering-adding-and-removing), [rest between
sets](#rest-between-sets) and [step notes](#step-notes). Which exercises the
notes were is behind `-v`, since notes only move when you edit
`workouts.yaml`.

An "Also in ..." section appears when a target that moved also exists in another
workout. See [shared exercises](progression.md#shared-exercises).

### Creating a workout

A config entry with no `garmin_workout_id` is one Garmin has not been told
about. `update --apply` builds it, and writes the id it is given back into
`workouts.yaml`:

```text
Creating: Workout C

+ Front Squat                              new at position 1, 4 x 6 x 40 kg
+ Romanian Deadlift                        new at position 2, 3 x 8 x 60 kg

Created Workout C (workout 1234567890)
Recorded its id in /home/you/workouts.yaml
```

The workout takes its Garmin name from `key`, every exercise starts at
`rep_low` and its `start_weight`, and progression takes over from the first
session you log against it. A dry run prints the same plan and creates nothing.

**Your config is rewritten to record it.** It is parsed, the id set under the
workout's `key`, and the whole document dumped back: every value and every key,
in the order you wrote them, whether or not this tool understands it. Comments
and blank lines are not part of the document and do not survive. `notes` is,
which is why it is there.

If the id cannot be written back - the file is read-only, say - the run stops
and says which id it could not record. The workout exists in Garmin by then,
and a run that shrugged that off would build a second copy of it next time.

### Ordering, adding and removing

The order of `exercises` in the config is the order of the workout. Reordering,
adding and removing all show up under their own markers:

```text
+ Front Squat                              new at position 4, 3 x 8 x 20 kg
- Leg Curl                                 removed: no longer in workouts.yaml
~ Plank                                    moved to position 1
```

| Marker | Meaning |
| --- | --- |
| `+` | The config names an exercise Garmin does not have, so it is built |
| `-` | Garmin has one the config no longer names, so it is dropped |
| `~` | It is still there, in a different place |

**An exercise moved keeps everything it had** - its target, its sets, its rest
and its notes travel with it, because the step itself is moved rather than
rebuilt.

**An exercise removed loses its target for good.** There is nowhere else that
number is stored. The dry run lists every removal before anything is written,
which is the moment to check that a `-` line is a decision and not a typo in a
`garmin_name`.

A plan that removes one exercise and adds another that looks like the same
movement says so, because that is what a mistyped `garmin_name` produces:

```text
  ! Lat Pull-down: added while LAT_PULLDOWN is removed, and the two look like
    the same exercise. If that is a renamed garmin_name rather than a swap, the
    target on LAT_PULLDOWN is about to be lost with it
```

It is a warning rather than a refusal, because deliberately swapping a movement
for a variant of it looks identical from here. Matching a name and matching a
category both have to fail before it can happen at all, so filling in
`garmin_category` is what stops most typos ever getting this far -
[`workout check`](#check) reports the ones it rescues.

Only genuine moves are reported. Adding an exercise at the top shifts the
position of everything under it without any of that being a move, so those
lines do not appear.

### Rest between exercises

Garmin separates exercises with a wait for the lap button. Set
`rest_between_exercises` on a workout and each of those becomes a countdown:

```text
* Between exercises                    lap button  ->  30 s rest     (8 gap(s), from workouts.yaml)
```

One line for the workout, however many gaps it has, because the config says it
once. Leave the key out and Garmin's own steps are left alone, whether they
wait for the button or were given a time in Connect.

`workout import` fills the key in when every gap in a workout agrees on one
fixed time, and leaves it out otherwise - one number cannot describe gaps that
differ, and guessing would change the others.

### Rest between sets

An exercise's `rest` in `workouts.yaml` is written to the Garmin workout, so
the file is where you change how long you rest:

```text
* Barbell Back Squat            120 s rest  ->  150 s rest    (rest from workouts.yaml)
```

Like the notes below, this is config-driven rather than earned: nothing in a
session moves a rest, so an edit to the file is on its own a reason to write a
workout. Leaving `rest` out of an exercise is having no opinion about it, and
Garmin's own value is kept.

**Only a rest Garmin counts down can be retimed.** Garmin stores the rest
between sets inside the repeat group, as either a fixed time or a wait for the
lap button. A button press is not an interval, and turning one into a countdown
would change how the workout is performed rather than correct a value, so it is
reported and left alone:

```text
  ! Barbell Back Squat: rest is not a fixed time in Garmin, left alone (wanted 150s)
```

Set that step to a timed rest in Garmin Connect if you want the config to drive
it. The rest *between exercises* is a separate step, driven by
[`rest_between_exercises`](#rest-between-exercises) and otherwise left alone.

**Every set gets its rest, including the last.** Connect can be told to drop
the rest that follows a repeat group's final set, which leaves one exercise
behaving unlike the others in the same workout. An exercise's `rest` means
every set of it, so a group set to skip is put back:

```text
* Weighted Standing Calf Raise  no last rest  ->  rest after every set  (was skipping the last rest)
```

Applied to every exercise, whether or not it declares a `rest`: how long to
rest is the config's to say, but whether the last set gets one at all is not a
setting this tool offers. Turn it back on in Connect and the next run undoes
it.

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

If a push seems not to have arrived, `-v` reads the queue back afterwards,
which is the only way to confirm something is actually waiting for the device:

```text
2 message(s) now waiting for your device(s).
```

The queue drains when the watch syncs, so a count of zero after a sync is the
expected result rather than a sign the push failed.

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
overwrite, so a config you have already tuned is safe.

Garmin knows less than this tool needs, so three fields are inferred and want
checking:

| Field | How |
| --- | --- |
| `rep_low` | Garmin's current target becomes the bottom of the range |
| `rep_high` | A suggestion: `rep_low` plus a few. **Check it** |
| `load` | Guessed from the exercise name (`BARBELL_*`, `DUMBBELL_*`, `CABLE_*`), else `machine` if loaded and `bodyweight` if not |

Each exercise says what was guessed about it in its own `notes`, since a
generated file is written by a YAML dumper and has nowhere to put a comment.
Clear the `notes` once you have checked the entry, or keep your own reminders
there.

`rep_step`, `weight_step` and `start_weight` have no Garmin equivalent at all
and are left to you. Everything else - `sets`, `rest`, `unit`,
`garmin_name`, `garmin_category`, `garmin_workout_id` - is read straight from
the payload, as is `rest_between_exercises` when every gap in the workout
agrees on one fixed time.

Garmin's API has no server-side name search, so `--name` filters locally.

## check

```bash
workout check
```

Answers one question: **can the config still name the exercises it thinks it
is naming?** It does not tell you what `update` would change - that is what
`update` itself prints, and anything it can fix is not drift to report here.

**Worth running before an `update --apply` you are unsure of**, because a wrong
`garmin_name` does not fail loudly - matching falls back to `garmin_category`,
so the run keeps working until the fallback stops working too:

```text
Workout A (111111111)
   ! Standing Calf Raise: config says WEIGHTED_STANDING_CALF_RAISE, Garmin says
     STANDING_CALF_RAISE. Matched by category CALF_RAISE, so it works, but the
     name is wrong
```

That matters more than it used to. An exercise the config does not name is now
**removed** rather than warned about, so a `garmin_name` with nothing to bridge
it costs you the target stored in that step:

```text
   !! Face Pull: FACE_PULL is not in the Garmin workout at all, so `update`
      would build a new step for it and drop the one Garmin has
```

| Reported | Meaning |
| --- | --- |
| `!` | The name is wrong but the category rescued it. Works today, breaks the day a second exercise claims that category |
| `!!` | Nothing in Garmin answers to it, or the category is ambiguous. `update` would drop a step and build another |

A workout with no `garmin_workout_id` yet is reported as "not in Garmin yet"
rather than checked, there being nothing to compare it against.

**Everything it reports needs a hand, so any finding at all exits non-zero.**
That is what makes it worth putting in a cron job: it goes off when the config
is wrong, not when you have edited a rest and not yet run `update`.

Set counts, rests, and exercises the config no longer names are all things
`update` applies for you, so they are not findings. `update --dry-run` lists
them, with what each would become.

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
