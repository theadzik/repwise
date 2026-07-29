# Troubleshooting

- [Authentication](#authentication)
- [Common symptoms](#common-symptoms)
- [Nothing matched my session](#nothing-matched-my-session)
- [An exercise was skipped](#an-exercise-was-skipped)
- [Known limitations](#known-limitations)

## Authentication

The first command that reaches Garmin prompts for your email, password and MFA
code, then caches OAuth tokens in `settings.garmin.token_store`
(`~/.garminconnect` by default). Later runs reuse those and never touch the
login endpoint, which is what keeps you clear of Garmin's login rate limits.

Delete that directory to force a fresh login.

Credentials are never stored by this tool - only the tokens Garmin issues.

Reusing a cached session is routine, so it is only reported under `--verbose`:

```text
$ workout list -v
DEBUG   workout.garmin.client: Resumed cached session.
```

## Common symptoms

| Symptom | Cause and fix |
| --- | --- |
| `429` / rate limited | Too many login attempts from your IP. Wait it out; once tokens are cached the login endpoint is skipped entirely |
| `401` after working before | Stale tokens. Delete the token store and log in again |
| Cloudflare challenge | Only affects browser automation. This tool goes through `garminconnect`, built on `curl_cffi`, which is not subject to it |
| Every exercise looks like bodyweight | The weight is reading as zero. Check a `--dump` against [Garmin's API](garmin-api.md#weight-units) |
| `--push` refused with exit 3 | `--push` needs `--apply`; without it nothing has been written, so there is nothing to send |
| `workouts.yaml does not exist yet` | Copy the example: `cp workouts.example.yaml workouts.yaml` |

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
workout update --activity 1234567890
```

Prefixes are matched case-insensitively against the start of the name, so
`workout a` matches "Workout A - evening".

## An exercise was skipped

```text
! Standing Calf Raise: not found in the activity, skipped
! STANDING_CALF_RAISE: not in workouts.yaml, skipped
```

The first means the exercise is in your config and in the Garmin workout, but
you did not perform it in that session. Harmless if you skipped it.

The second means the Garmin workout contains an exercise your config does not
describe. Add it, or ignore the warning if you do not want it progressed.

Either can also mean the identifiers disagree. Run
[`workout check`](commands.md#check), which is built for exactly this, and see
[finding your exercise
identifiers](configuration.md#finding-your-exercise-identifiers).

Warnings never fail a run silently - a skipped exercise is always reported.

## Known limitations

- `rest` and `video` are documentation only; they are not written back to
  Garmin. Only rep, time and weight targets are.
- Workouts must already exist in Garmin Connect. This tool updates them, it
  does not create them.
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
