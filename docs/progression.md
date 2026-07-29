# Progression

How the next target is decided, in full. If a run changed something you did not
expect, the answer is here.

- [The five rules](#the-five-rules)
- [Progress is judged by the weakest set](#progress-is-judged-by-the-weakest-set)
- [Working weight](#working-weight)
- [A load has to be earned](#a-load-has-to-be-earned)
- [Decision order](#decision-order)
- [Worked examples](#worked-examples)
- [Timed holds](#timed-holds)
- [Alternating exercises](#alternating-exercises)
- [Shared exercises](#shared-exercises)
- [No state file](#no-state-file)

## The five rules

Double progression: reps go up first, weight second.

1. Start at the lower end of the range, e.g. `6-6-6` for a range of 6-10.
2. Each workout, add a rep to every set: `7-7-7`, then `8-8-8`.
3. Once every set reaches the upper end, add weight and reset to the lower end.
4. If you didn't match the previous result, repeat it unchanged.
5. A load is only adopted once it can be carried for `rep_low`. Lift something
   other than what was prescribed and fall short of the range and the previous
   target stands.

## Progress is judged by the weakest set

The new rep target is `min(reps) + 1`, not `previous target + 1`.

Doing 7,7,10,10 against a target of 7 advances to 8 - the same as 7,7,7,7. Extra
reps on the easy sets earn nothing while the floor stays put, because the next
target has to be achievable on *every* set. Prescribing 11 when two sets managed
only 7 would just guarantee a miss.

But beating the target everywhere does count: 8,8,8,8 advances to 9, and
10,10,10,10 tops out the range and earns the weight jump even though only 7 was
asked for.

## Working weight

A session may use more than one load for the same exercise - maxing out a set
that felt light, then adding weight for the rest.

Rather than averaging, the **most common load across the sets** is taken as the
working weight, ties going to the heavier one, and progression is judged only
among the sets at that load. A single lighter opening set therefore cannot drag
the baseline back down.

Everything then rebases onto that load, even if it differs from what the Garmin
workout still has stored. Bump the weight mid-session and the new weight is
banked rather than discarded.

## A load has to be earned

Rebasing has one limit: **a load is only adopted if the weakest set at it still
reached `rep_low`.** Fall short of the range and the previous target stands,
weight and reps both.

Without that, any weight you happened to lift became the new prescription. A
lateral raise programmed 12-15 at 3 kg, done as 3x8 at 4 kg because the 3 kg
pair was taken, would come back as `9 x 4 kg` - a target below the range you
programmed, off the back of a jump you had not earned. What you get instead is:

```text
  Dumbbell Lateral Raise      13 x 3 kg  ->  13 x 3 kg   (only 8 at 4 kg, below the 12-15 range, keep 13 x 3 kg)
```

This is checked before the set count, so a heavier load that was only managed
for some of its sets is not banked either.

It applies in both directions - a deload rebases downward only while it still
lands in the range - but only when the load changed. At an unchanged load a
short session is already rule 4's "missed target, repeat".

Persistent rejections mean `weight_step` is too big for the range: at 3 kg a
1 kg dumbbell step is a 33% jump, which a 12-15 range cannot absorb. Widen the
range or micro-load. Isolation work usually wants a wider range than a barbell
compound for exactly this reason.

## Decision order

Given the exercise's config, the target currently stored in Garmin, and the sets
actually performed:

| # | Condition | Result |
| --- | --- | --- |
| 1 | No sets logged | Unchanged |
| 2 | Weight changed, floor below `rep_low` | Unchanged (rule 5) |
| 3 | Fewer than `sets` at the working weight | Bank the weight, consolidate reps |
| 4 | Same weight, floor below target | Repeat unchanged (rule 4) |
| 5 | Floor at or above `rep_high` | `rep_low` at weight + step (rule 3) |
| 6 | Otherwise | `floor + rep_step` at the working weight (rule 2) |

Bodyweight exercises never reach case 5's weight increase; they target
`rep_high` and hold.

Case 6 caps at `rep_high`, so an off-step target cannot overshoot the range.
Together with case 2, a target can never leave the programmed range.

## Worked examples

A squat, range 6-10, 4 sets, 2.5 kg step, stored target 7 x 20 kg:

| Performed | Next target | Why |
| --- | --- | --- |
| 7,7,7,7 @ 20 | 8 x 20 | Matched, add a rep |
| 7,7,10,10 @ 20 | 8 x 20 | Weakest set still 7 |
| 8,8,8,8 @ 20 | 9 x 20 | Beat it everywhere |
| 10,10,10,10 @ 20 | 6 x 22.5 | Topped the range |
| 7,7,7,5 @ 20 | 7 x 20 | Missed, repeat |
| 10 @ 20, then 8,8,8 @ 22.5 | 8 x 22.5 | Only 3 of 4 sets at 22.5, consolidate |
| 8,8,8,8 @ 22.5 | 9 x 22.5 | Rebased onto the heavier load |
| 8,8,8,8 @ 15 | 9 x 15 | Deload respected, not punished |
| 4,4,4,4 @ 25 | 7 x 20 | Below the range at 25, so 25 is not kept |

The last two rows are the pair worth knowing. A deload rebases the stored target
downward, so a bad day at a lighter weight moves the target with it - but only
while the reps stay in the range. Once they drop below `rep_low`, the load is
discarded instead and the stored target is left alone.

## Timed holds

An exercise with `unit: seconds` - a plank, typically - progresses on time
instead of reps, using the same rules. Hold the target on every set and it goes
up by one second; fall short and it repeats. At `rep_high` it holds there rather
than gaining load.

Garmin expresses this differently in its two payloads: the workout step ends on
`time` rather than `reps`, and the activity records `repetitionCount: 1` per set
with the real hold in `duration`. Those seconds are moved into the rep slot at
the boundary, so the rules need no special case - a 47 s target held for 46 s
reads as a missed rep and repeats.

Durations are rounded to whole seconds, since Garmin logs them as floats
(`46.0`, `48.072`).

## Alternating exercises

Rep ranges for unilateral work are conventionally written per side, but the
watch counts each side separately - it logs 20 for ten alternating lunges. You
can either correct the count after every session, or program the exercise in
Garmin's units and let the tool read the watch as-is.

For the second option, double the range (8-12 per leg becomes 16-24) and set
`rep_step: 2`. Without the step a plain +1 would advance only one side and leave
the target on an odd number; with it the ladder runs 16, 18, 20, 22, 24, then a
weight jump back to 16 - the same real rate, both sides even.

Keep `(rep_high - rep_low)` a multiple of `rep_step` so stepping lands exactly
on `rep_high` and earns the weight jump rather than straddling it.

## Shared exercises

An exercise can appear in more than one workout - a calf raise in both days of
an A/B split, say. When a target moves, the same target is pushed into every
other workout containing that exercise, so the copies cannot drift apart. Those
workouts are fetched and written too, and the run lists them separately:

```text
Also in Workout A (workout 222222222):
* Standing Calf Raise    12 x 0 kg  ->  12 x 20 kg  (synced from Workout B)
```

The decision is copied verbatim rather than recomputed against the other
workout's history, which is what keeps the copies identical.

When one run covers several sessions, they are replayed oldest first, so if two
of them moved the same shared exercise the more recent session's decision is
the one that stands.

Matching uses the same name-then-category rule as everywhere else, so
`garmin_name` must agree between the two entries. A shared exercise must also
have the same `rep_low`, `rep_high` and `rep_step` everywhere; the config is
rejected outright if they differ, since a synced target could otherwise land
outside one workout's range.

## No state file

Nothing is stored between runs. The Garmin workout holds the current target and
the activity holds what was performed - together they answer everything.

That is why you can edit a target by hand in Garmin Connect and the next run
simply picks up from there.
