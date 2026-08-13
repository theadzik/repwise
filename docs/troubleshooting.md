# Troubleshooting

- [Authentication](#authentication)
- [Common symptoms](#common-symptoms)
- [Nothing matched my session](#nothing-matched-my-session)
- [An exercise was skipped](#an-exercise-was-skipped)
- [An exercise disappeared from my workout](#an-exercise-disappeared-from-my-workout)
- [A workout was created twice](#a-workout-was-created-twice)
- [Known limitations](#known-limitations)

## Authentication

The first command that reaches Garmin prompts for your email, password and MFA
code, then caches OAuth tokens in `settings.garmin.token_store`
(`$XDG_CONFIG_HOME/repwise`, i.e. `~/.config/repwise`, by default - beside your
config). Later runs reuse those and never touch the login endpoint, which is
what keeps you clear of Garmin's login rate limits.

Run [`repwise logout`](commands.md#logout) to force a fresh login.

### Upgrading from a version that defaulted to `~/.garminconnect`

Nothing to do today. If your config names no `token_store` of its own, and
there are tokens in the old directory and none in the new one, they are used
where they lie and the run says so:

```text
Using the Garmin tokens in /home/you/.garminconnect, which is where repwise
used to keep them. The default is now /home/you/.config/repwise.
    mkdir -p /home/you/.config/repwise && mv /home/you/.garminconnect/* /home/you/.config/repwise/
Or name the old directory in workouts.yaml, under settings.garmin:
    token_store: ~/.garminconnect
Deprecated: repwise 2.0 drops this fallback and uses the new default regardless
of what is in the old directory.
```

Take either way out. Run the `mv` and the warning stops, or set
`settings.garmin.token_store: ~/.garminconnect` and keep the old location for
good. Logging in again works too - the new tokens land at the new default, and
the old directory stops being consulted the moment it does.

Whichever you pick, **delete `~/.garminconnect` once you are done with it.** The
token left in it stays valid until it expires, and nothing is watching it any
more. A config that names its own `token_store` never sees any of this.

### What is stored, and what it is worth

Your email and password are typed at the prompt, handed straight to Garmin, and
never written anywhere. What is written is `garmin_tokens.json` in the token
store, holding the tokens Garmin issues in exchange:

```json
{"di_token": "...", "di_refresh_token": "...", "di_client_id": "..."}
```

**Treat that file as a password.** It is not one - but until it expires it is a
bearer credential for the account, and using it needs neither your password nor
an MFA code. Anything that can read the file can read your Garmin data and
write to your workouts.

What that means in practice:

| Where you stand | What it means |
| --- | --- |
| It is written `0600` inside a `0700` directory | So other accounts on the machine cannot read it. `garminconnect` enforces this on every write |
| repwise warns if it finds it otherwise | A file restored from a backup, copied between machines, or written by an older version can have looser permissions. The warning names the `chmod` that fixes it, and never runs it for you |
| Keep it out of backups and dotfile repos | This is the realistic way it escapes, not another user on your laptop |
| `repwise logout` deletes it | The token file only; the cached exercise catalog beside it is a copy of a public file and is left alone |
| Nothing revokes it at Garmin's end | `logout` removes this machine's copy. A copy taken before that stays valid until it expires, and Garmin exposes no per-token revocation to repwise. If you think one has escaped, change your Garmin password |
| Full-disk encryption is what protects a stolen laptop | File permissions do not, and repwise does not encrypt the file itself |

Reusing a cached session is routine, so it is only reported under `--verbose`:

```text
$ repwise list -v
DEBUG   repwise.garmin.client: Resumed cached session.
```

## Common symptoms

| Symptom | Cause and fix |
| --- | --- |
| `429` / rate limited | Too many login attempts from your IP. Wait it out; once tokens are cached the login endpoint is skipped entirely |
| `401` after working before | Stale tokens. Run `repwise logout` and log in again |
| `can be read by other users` | The token file or its directory has picked up looser permissions than it was written with. The warning names the `chmod` that fixes it. See [what is stored](#what-is-stored-and-what-it-is-worth) |
| `no terminal to log in from` | A scheduled run found no cached session. Run it once by hand to cache the tokens |
| Cloudflare challenge | Only affects browser automation. This tool goes through `garminconnect`, built on `curl_cffi`, which is not subject to it |
| Every exercise looks like bodyweight | The weight is reading as zero. Check a [`fetch activities`](commands.md#fetch-activities) dump against [Garmin's API](garmin-api.md#weight-units) |
| `--push` refused with exit 3 | `--push` needs `--apply`; without it nothing has been written, so there is nothing to send |
| A correction made in Connect changed nothing | With [`activity_caching`](configuration.md#reusing-what-is-on-disk) on, a session is re-read when Garmin's totals for it move. An edit that moves none of them is invisible; `repwise fetch activities --force`, or deleting that session's files from `dump_dir`, downloads it again |
| `No workouts.yaml found` | The message lists every path tried. Put one there, copy the example, or pass `--config PATH`. See [where the file lives](configuration.md#where-the-file-lives) |

`garth` is deprecated and unmaintained after Garmin's March 2026 auth change.
`garminconnect` >= 0.3.5 rebuilt its login on `curl_cffi` and is the supported
path - do not pin below that.

## Nothing matched my session

```text
No recent activity matching ['workout a', 'workout b']. Pass --activity <id> to
choose one explicitly.
```

Either the activity is named differently than your `activity_prefixes` expect,
or it is older than `settings.garmin.activity_search_limit` activities ago.

Check what Garmin actually called it, then either add that prefix to the config
or pass the id directly:

```bash
repwise update --activity 1234567890
```

Prefixes are matched case-insensitively against the start of the name, so
`workout a` matches "Workout A - evening".

## An exercise was skipped

```text
! Standing Calf Raise: not found in the activity, skipped
```

The exercise is in your config and in the Garmin workout, but you did not
perform it in that session. Harmless if you skipped it.

It can also mean the identifiers disagree - the exercise *was* performed, under
a name neither `garmin_name` nor `garmin_category` matches. Run
[`repwise check`](commands.md#check), which is built for exactly this, and see
[finding your exercise
identifiers](configuration.md#finding-your-exercise-identifiers).

An exercise in the Garmin workout that your config does not describe is no
longer a warning: it is [removed](#an-exercise-disappeared-from-my-workout),
because the config decides what the workout holds.

Warnings never fail a run silently - a skipped exercise is always reported.

## An exercise disappeared from my workout

The config drives the workout, so an exercise it does not name is removed from
Garmin - **and the target stored in that step goes with it.** There is nowhere
else that number is kept.

Usually that is what you asked for. When it is not, the cause is almost always
a `garmin_name` that no longer matches: the config names an exercise Garmin
does not have, so it is built, and the one Garmin has goes unnamed, so it is
dropped. A dry run shows a `+` row and a `-` row for what should have been the
same exercise - and where the two look like one movement, says so on a single
row in the exercise's own place, and warns at the end:

```text
+ 3 Lat Pull-down build  3            ->  8 x 50 kg        replaces LAT_PULLDOWN
! Lat Pull-down replaces LAT_PULLDOWN: if that is a renamed garmin_name rather than a swap, its target is lost
```

Two things stop it getting that far. Filling in `garmin_category` bridges a
mistyped name whenever exactly one exercise in the workout claims that
category, and [`repwise check`](commands.md#check) reports the names it is
rescuing, before the day a second exercise claims the same category and it
stops being able to.

## A workout was created twice

A workout is created when its config entry has no `garmin_workout_id`, and the
id is written straight back into the file. Two copies means that write-back did
not happen: the run says so and stops, naming the id it could not record.

Add it by hand and the extra copy stops being created:

```yaml
  - key: Workout C
    garmin_workout_id: "1234567890"
```

Then delete whichever copy you do not want in Garmin Connect, keeping the one
whose id is in the file.

## Known limitations

- `notes` is yours alone; it is not written back to Garmin, and not to be
  confused with the step note the watch shows, which this tool composes from
  the rep range and weight step. Rep, time and weight targets, that step note,
  `sets`, `rest` and `rest_between_exercises` are all written to Garmin.
- Recording a workout id rewrites the config file, so comments and blank lines
  in it are lost the first time a workout is created. Values, ordering and
  unrecognised keys all survive; put anything worth keeping in `notes`.
- An exercise's own `rest` can only be retimed where Garmin already counts one
  down; a lap-button rest between sets is reported and left alone.
  `rest_between_exercises` does convert one, that being the point of the key -
  see [rest between exercises](commands.md#rest-between-exercises).
- Connect's switch for dropping the rest after a repeat group's last set is
  turned back off on every run, and there is no config key to keep it on: an
  exercise's `rest` is meant for every set of it. See [rest between
  sets](commands.md#rest-between-sets).
- An exercise Garmin holds outside a repeat group has nowhere to keep a set
  count, so a `sets` above 1 is reported and left alone. Connect builds a group
  even for a single set, so this is unlikely to come up.
- Deleting a workout from `workouts.yaml` does not delete it from Garmin.
  Removing an exercise is reversible by hand; deleting a workout would take its
  history with it, so it is left to you.
- Every set of an exercise gets the same target, matching the "same reps on
  every set" model. Per-set targets are not supported.
- The first matching activity within `activity_search_limit` is used; older
  sessions need `--activity`.
- A deload rebases the stored target downward, so a bad day at a lighter weight
  moves the target with it - unless it falls below `rep_low`, in which case the
  load is [not adopted at all](progression.md#a-load-has-to-be-earned).
- A too-large `weight_step` shows up as a plateau rather than an error: the run
  keeps repeating the same target, reporting either "missed target" or "below
  the ... range". Lower the step or widen the range by hand when that happens.
