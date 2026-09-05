# Commands

Full reference. Every command accepts `--config` and `--verbose`. `--config`
belongs to the top level, so it goes before the command name; `--verbose` reads
either side of it:

```bash
repwise --config /path/to/other.yaml update
repwise update --verbose
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
- [fetch](#fetch) - download raw payloads, or the exercise catalog
- [logout](#logout) - forget the cached Garmin session
- [completion](#completion) - Tab completion for bash and zsh
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
repwise update                    # dry run, shows changes
repwise update --apply            # write to Garmin
repwise update --apply --push     # and send them to your watch
repwise update --activity 1234    # use a specific activity
```

| Flag | Effect |
| --- | --- |
| *(none)* | Dry run. Prints the plan and writes nothing |
| `--apply` | Write to Garmin Connect: new targets, and any workout the config creates or reshapes |
| `--push` | Queue the written workouts for your devices. Requires `--apply` |
| `--activity ID` | Update from this one activity instead of scanning |

**Dry run is the default.** Nothing is sent to Garmin without `--apply`, and
nothing is written back to `workouts.yaml` either.

### Choosing the activities

Without `--activity`, each workout gets its own most recent activity: the
latest one whose name starts with any of that workout's `activity_prefixes`,
searching back `settings.garmin.activity_search_limit` activities. Prefixes are
matched case-insensitively, so `["workout a", "trening a"]` catches either
spelling.

With
[`activity_caching`](configuration.md#reusing-what-is-on-disk) on, that same
list decides what to file: every strength session in it that `dump_dir` does
not already hold is downloaded before anything is worked out, and the run then
reads sessions off disk rather than asking for them one at a time.

```text
Filing 2 session(s) into ./dumps
```

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
`--activity` and its id, which Garmin Connect URLs and
[`fetch activities`](#fetch-activities) filenames both contain. Do that before
the later session's run, or apply them in order.
Re-processing an activity is safe: the second pass reads its own applied target
and reports "missed target" rather than advancing again.

### Reading the output

```text
Activity: Workout A (1234567890)
Updating: Workout A -> workout 111111111

  # EXERCISE                     ACTION  SETS       BEFORE      AFTER      CONFIG         WHY
* 1 Barbell Back Squat           advance 3 -> 4  8 x 30 kg  ->  9 x 30 kg  sets rest note add 1 rep
* 2 Sit-up                       hold    3         11 reps  ==  11 reps    note           missed target, 10 on the worst set
* 3 Weighted Standing Calf Raise advance 4      12 x 30 kg  ->  13 x 30 kg note           add 1 rep
* 4 Plank                        hold    3                                 note           from workouts.yaml
+ 5 Leg Press                    build   3                  ->  6 x 60 kg                 new in workouts.yaml
-   FACE_PULL                    drop                                                     no longer in workouts.yaml
! Plank: not found in the activity, skipped

Dry run: 2 step(s) would change, 2 exercise(s) would be added, removed or moved, 1 set count(s) would change, 1 rest time(s) would change, 4 note(s) would be refreshed. Re-run with --apply.
```

One table per workout that had a session, oldest first, each headed by the
activity it learned from. A workout the config changed but no session touched
gets a table of its own, headed `Shaping:` or `Creating:` instead.

**One exercise, one row**, in the order `exercises` puts it in, whatever the run
decided about it. Every column says one thing:

| Column | What it holds |
| --- | --- |
| *(marker)* | `*` this run writes it, *(space)* read and left alone, `+` `-` `~` added, dropped or moved. See [ordering](#ordering-adding-and-removing) |
| `#` | Where it sits in the workout. Blank for an exercise being dropped |
| ACTION | What is happening: `advance` `hold` `ease` for a target, `build` `drop` `move` for the shape, `retime` for the rest between exercises |
| SETS | What the workout prescribes, and `3 -> 4` when you change it |
| BEFORE, AFTER | The target, in the unit the exercise is measured in - `6 x 65 kg` loaded, `11 reps` bodyweight, `47 s` for a timed hold. See [coming back from a stall](progression.md#coming-back-from-a-stall) for `8+2` |
| CONFIG | What `workouts.yaml` would rewrite here: `sets`, `rest`, `note`, `last-rest`. The file itself says what to, so only the fact is worth a column |
| WHY | The session's verdict, or where an exercise came from, or the config being the reason |

An exercise the config no longer names comes after the ones it does, the rest
between exercises last of all, and warnings after everything, since those are
the lines that go to stderr. Columns are as wide as their widest cell, so a
workout of short names is not read across a gap of spaces.

An `ACTION` of `hold` with nothing in `CONFIG` and a blank marker is the one row
you can skip: read, judged, nothing to write.

The closing line counts everything else the run would touch:

```text
Dry run: 8 step(s) would change, 2 exercise(s) would be added, removed or moved, 1 set count(s) would change, 1 rest time(s) would change, 1 step(s) would stop skipping their last rest, 17 note(s) would be refreshed. Re-run with --apply.
```

Each clause appears only when it applies, and every exercise it counts has a
line of its own above it. See [ordering, adding and
removing](#ordering-adding-and-removing), [rest between
sets](#rest-between-sets) and [step notes](#step-notes).

An "Also in ..." section appears when a target that moved also exists in another
workout on the same `load`. See [shared
exercises](progression.md#shared-exercises).

### Creating a workout

A config entry with no `garmin_workout_id` is one Garmin has not been told
about. `update --apply` builds it, and writes the id it is given back into
`workouts.yaml`:

```text
Creating: Workout C

  # EXERCISE          ACTION SETS BEFORE      AFTER     CONFIG WHY
+ 1 Front Squat       build  4            ->  6 x 40 kg        new in workouts.yaml
+ 2 Romanian Deadlift build  3            ->  8 x 60 kg        new in workouts.yaml

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
  # EXERCISE    ACTION SETS BEFORE      AFTER     CONFIG WHY
~ 1 Plank       move   3                                 from position 3
+ 3 Front Squat build  4            ->  8 x 20 kg        new in workouts.yaml
-   LEG_CURL    drop                                     no longer in workouts.yaml
```

| Marker | ACTION | Meaning |
| --- | --- | --- |
| `+` | `build` | The config names an exercise Garmin does not have, so it is built at the bottom of its range |
| `-` | `drop` | Garmin has one the config no longer names, so it is dropped. It has no `#`, being nowhere in the workout now |
| `~` | `move` | It is still there, in a different place. `#` is where it is now, and WHY says where it came from |

**An exercise moved keeps everything it had** - its target, its sets, its rest
and its notes travel with it, because the step itself is moved rather than
rebuilt.

**An exercise removed loses its target for good.** There is nowhere else that
number is stored. The dry run lists every removal before anything is written,
which is the moment to check that a `-` row is a decision and not a typo in a
`garmin_name`.

A plan that removes one exercise and adds another that looks like the same
movement says so, because that is what a mistyped `garmin_name` produces. It is
one row, where the new exercise sits, plus a warning at the end:

```text
+ 3 Lat Pull-down build  3            ->  8 x 50 kg        replaces LAT_PULLDOWN
! Lat Pull-down replaces LAT_PULLDOWN: if that is a renamed garmin_name rather than a swap, its target is lost
```

The removal has no `-` row of its own: the exercise taking over from it says so
already.

It is a warning rather than a refusal, because deliberately swapping a movement
for a variant of it looks identical from here. Matching a name and matching a
category both have to fail before it can happen at all, so filling in
`garmin_category` is what stops most typos ever getting this far -
[`repwise check`](#check) reports the ones it rescues.

Only genuine moves are reported. Adding an exercise at the top shifts the
position of everything under it without any of that being a move, so those
lines do not appear. Where more than one set of moves explains the new order -
swapping the second exercise with the fourth is two moves whichever two you
name - the ones reported are those no longer at the position they were at, so
the exercise they crossed is left out of it.

### Rest between exercises

Garmin separates exercises with a wait for the lap button. Set
`rest_between_exercises` on a workout and each of those becomes a countdown:

```text
*   Between exercises retime      lap button  ->  30 s rest        8 gap(s), from workouts.yaml
```

One row for the workout, however many gaps it has, because the config says it
once. Leave the key out and Garmin's own steps are left alone, whether they
wait for the button or were given a time in Connect.

`repwise import` fills the key in when every gap in a workout agrees on one
fixed time, and leaves it out otherwise - one number cannot describe gaps that
differ, and guessing would change the others.

### Rest between sets

An exercise's `rest` in `workouts.yaml` is written to the Garmin workout, so
the file is where you change how long you rest:

```text
* 1 Barbell Back Squat advance 4    8 x 30 kg  ->  9 x 30 kg rest   add 1 rep
```

`rest` in the CONFIG column is the whole report of it: the file says how long,
and repeating that here would only say it twice. Like the notes below, this is
config-driven rather than earned: nothing in a
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
* 3 Weighted Standing Calf Raise hold   4                      last-rest from workouts.yaml
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

A note that would move puts `note` in the exercise's CONFIG column, so a run
whose targets all held still still says what it is about to write:

```text
* 1 Barbell Back Squat hold   4    7 x 30 kg  ==  7 x 30 kg note   up to date
```

The note itself is not printed: `workouts.yaml` decides it, and the shape above
says what it will read.

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
repwise update --apply --push
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
repwise list          # your strength workouts and their ids
repwise list --all    # every workout, whatever the sport
```

Use it to find the `garmin_workout_id` values for your config, or to confirm
what Garmin actually has.

## import

Turns existing Garmin workouts into a config file.

```bash
repwise import                    # print to stdout
repwise import -o workouts.yaml   # write to a file
repwise import --name "Workout A" # only workouts matching a substring
repwise import --id 111111111     # only this workout
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
repwise check
```

Answers three questions `update` does not. **Do the exercises the config names
exist at all?**, **can the config still name the exercises it thinks it is
naming?**, and **does every rep range still fit what its weight step is
worth?** - the last is covered under [does the range fit the
step](configuration.md#does-the-range-fit-the-step). None of them tells you
what `update` would change: that is what `update` itself prints, and anything
it can fix is not drift to report here.

### Names Garmin has never heard of

The first question is asked of [Garmin's exercise
catalog](garmin-api.md#the-exercise-catalog): every exercise it knows, and the
category each is filed under. Garmin validates the two against each other, so a
real exercise under the wrong category is as unusable as an invented one - and
`update` cannot build either, which means the step never reaches the watch no
matter how often you sync.

```text
Workout B (222222222)
   !! Barbell Deadlift: BARBELL_DEADLIFT is filed under DEADLIFT, not SQUAT.
      Garmin checks the pair, so set garmin_category: DEADLIFT
   !! Face Pull: FACE_PULLL is not an exercise Garmin has. Did you mean
      FACE_PULL or FACE_PULL_WITH_EXTERNAL_ROTATION?
```

The catalog is downloaded the first time a command needs it and cached in
`settings.garmin.token_store`, so this costs one request ever. Refresh it with
[`repwise fetch exercises`](#fetch) when Garmin adds exercises. If it cannot be
downloaded, the names go unchecked, the rest of the checks still run, and the
warning names the command that retries it - `check` is worth running with no
network at all.

**This is the one check that says something useful about a workout Garmin does
not hold yet**, which is exactly when it pays: the names are wrong before the
workout is built rather than after.

### Names that match by luck

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
| `!!` | Garmin has no such exercise, or not under that category; or nothing in the workout answers to it; or the category is ambiguous |

An id naming a workout the account does not have - one deleted in Connect,
usually - is reported as that rather than as a failure to read it:

```text
Workout A (111111111)
   !! garmin_workout_id 111111111 is not in your Garmin account - delete the id
      to have it created again
```

A workout with no `garmin_workout_id` yet has its names checked and nothing
else, there being nothing in Garmin to compare the rest against:

```text
Workout C (not in Garmin yet)
  ok
```

**Everything it reports needs a hand, so any finding at all exits non-zero.**
That is what makes it worth putting in a cron job: it goes off when the config
is wrong, not when you have edited a rest and not yet run `update`.

Set counts, rests, and exercises the config no longer names are all things
`update` applies for you, so they are not findings. `update --dry-run` lists
them, with what each would become.

## fetch

Downloads what Garmin holds as JSON. Mostly a connectivity check and a way to
inspect Garmin's schema by hand. The first word says what to download:

```bash
repwise fetch workouts          # the definitions this tool writes targets into
repwise fetch activities        # the sessions you performed
repwise fetch exercises         # Garmin's exercise catalog
```

`workouts` and `activities` both take ids to narrow them, and both land in
`settings.garmin.dump_dir`. `exercises` is a different download altogether and
is described [below](#fetch-exercises).

### fetch workouts

```bash
repwise fetch workouts          # every workout in workouts.yaml
repwise fetch workouts 111111111
```

One file per workout, `workout-<id>.json`. A workout in your config that Garmin
does not hold yet has no definition to download, so it is skipped rather than
reported.

### fetch activities

```bash
repwise fetch activities              # every strength session found
repwise fetch activities 1234567890   # one session, by id
repwise fetch activities --force      # including ones already on disk
```

Three files per session, because Garmin keeps it as three payloads and none of
them is the other two:

| File | What it holds |
| --- | --- |
| `activity-<id>.json` | The summary: what the session was called, when, how long |
| `sets-<id>.json` | What your watch recorded, set by set - reps, and [weight in grams](garmin-api.md#weight-units) |
| `executed-<id>.json` | The workout that session was *run against* |

The third is the only record of what a past session was **asked** for: the
definition Garmin stores holds the target for the *next* one, because
[`update`](#update) rewrote it once that session was read. A session performed
against no workout has no third file.

Without ids, the recent activities are scanned and the strength ones kept -
`settings.garmin.activity_search_limit` is how far back that reaches, and
raising it is how you get at older sessions. An id is downloaded as given,
whatever sport it was: naming one says more about what you want than its type
does, and an id is also the only way to reach a session past the search limit.

With
[`activity_caching`](configuration.md#reusing-what-is-on-disk) on, a session
the dump directory already holds whole is left alone:

```text
Already on disk: 23801650013
Saved Training B -> activity-23896913928.json, sets-…, executed-…

1 session(s) -> ./dumps
1 already on disk; --force downloads them again.
```

`--force` opens a session that reads nothing off disk, so everything named is
downloaded and replaced. It is the answer to an edit in Connect that
[the totals cannot see](configuration.md#when-a-copy-stops-being-true); with
caching off it changes nothing, because nothing was being skipped.

### fetch exercises

The single word `exercises` downloads something else
entirely: [Garmin's exercise catalog](garmin-api.md#the-exercise-catalog),
every exercise it knows and the category each is filed under.

```bash
repwise fetch exercises
```

```text
Saved 1510 exercises in 47 categories -> /home/you/.config/repwise/exercises.json
```

It lands in `settings.garmin.token_store`, beside the cached OAuth tokens,
because it is the same kind of thing: per-user, disposable, and not something
to edit. [`check`](#check) and [`update`](#update) both read it, and both
download it themselves the first time they need one - **so this is how you
refresh a stale copy, not something to run first.** Refreshing is
unconditional; a copy already there is replaced.

The catalog is a public file, so this is the one command that opens no session
and needs no login. It cannot be combined with ids: it shares a command with
the other two downloads and nothing else.

## logout

Deletes the OAuth tokens cached in `settings.garmin.token_store`, so the next
command that reaches Garmin asks for your email, password and MFA code again.

```bash
repwise logout
```

```text
Deleted /home/you/.config/repwise/garmin_tokens.json
The next command that reaches Garmin will ask you to log in.
This does not revoke the token at Garmin's end.
```

**Run it on a machine that should stop having access to your account** - a
shared box, a server you were trying something on, a laptop you are handing
over. Until it expires, the cached token is as good as being logged in: see
[what is stored, and what it is
worth](troubleshooting.md#what-is-stored-and-what-it-is-worth).

Three things it deliberately does not do:

- **It does not revoke anything at Garmin.** Garmin issued the token and offers
  nothing to hand it back through, so this removes *this machine's copy*. A
  copy taken from the file beforehand stays usable until it expires; a password
  change is the strongest lever you have if you think one escaped.
- **It does not delete the exercise catalog** cached beside the token, which is
  a copy of a public file. That is the difference between this and `rm -rf`ing
  the token store: the next [`check`](#check) does not have to download it
  again.
- **It does not touch `workouts.yaml`.** Your routine is not a credential.

The file being gone afterwards is checked, not assumed. `garminconnect`
declines to delete through a token store it will not touch and says so only
under `--verbose`, so a `logout` that returned is not on its own proof of
anything. If the file survives, the command fails and names it rather than
printing a `Deleted` line about a credential that is still on disk.

Being signed out already is not a failure - it is the state the command exists
to reach - so a token store with nothing in it says so and exits `0`:

```text
No cached session in /home/you/.config/repwise, so nothing to do.
```

It opens no session, and needs no network. It does read your config, because
that is where the token store's location is written; `--config PATH` applies as
it does everywhere else.

## completion

Writes a Tab completion script to stdout, for `bash` or `zsh`.

```bash
repwise completion bash
repwise completion zsh
```

Load it from your shell's startup file:

```bash
# ~/.bashrc
source <(repwise completion bash)

# ~/.zshrc, after compinit
source <(repwise completion zsh)
```

That regenerates the script on every shell, so an upgrade needs nothing done
to it. To avoid the cost instead, write it out once - and remember to do it
again after upgrading:

```bash
repwise completion bash > ~/.local/share/bash-completion/completions/repwise
repwise completion zsh  > ~/.zsh/completions/_repwise   # a directory on $fpath
```

Under zsh, either has to come after `compinit`, which is what defines the
`compdef` the script ends with. Sourced too early it says so rather than
failing quietly.

What it completes:

| Typed | Offered |
| --- | --- |
| `repwise <TAB>` | the commands |
| `repwise update --<TAB>` | that command's options, and no others |
| `repwise --config <TAB>` | files |
| `repwise import -o <TAB>` | files |
| `repwise fetch <TAB>` | `exercises` |
| `repwise completion <TAB>` | `bash`, `zsh` |

**Workout and activity ids are deliberately not completed.** The only place to
look one up is Garmin, and that means a login: `repwise list` is how you find a
workout id, and a press of Tab should not reach the network or prompt for a
password. For the same reason the command itself reads no config, opens no
session and needs no network, so it is safe in a startup file that runs in
whatever directory a shell happens to open in.

The script is generated from the same parser that produces `--help`, so it
describes the version of repwise that printed it. It cannot list a flag that
does not exist, or miss one that does.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Nothing usable in the activity, or a fetch failed |
| `2` | Rate limited by Garmin |
| `3` | Bad configuration, or `--push` without `--apply` |

`check` also exits non-zero when it finds real drift, which is what makes it
usable from a scheduler.
