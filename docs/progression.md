# Progression

How the next target is decided, in full. If a run changed something you did not
expect, the answer is here.

- [The five rules](#the-five-rules)
- [Coming back from a stall](#coming-back-from-a-stall)
- [Deloading](#deloading)
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
2. Each workout, add a rep to every set: `7-7-7`, then `8-8-8` - or to only
   some of them, when you are coming back from a stall.
3. Once every set reaches the upper end, add weight and reset to the lower end.
4. If you didn't match the previous result, repeat it unchanged - and if you
   miss the same target twice, give something back.
5. A load is only adopted once it can be carried for `rep_low`. Lift something
   other than what was prescribed and fall short of the range and the previous
   target stands.

## Coming back from a stall

Rule 2 works while you are progressing smoothly. It works badly after a miss:
you fail `9-9-9`, repeat it, finally hit it, and are immediately asked for
`10-10-10` - the same jump that just failed, from a position you only barely
reached. So the size of the step depends on the run of misses behind it:

```text
advance = sets - misses in a row, at least 1
```

An advance is a rep on **one** set, taken from the first set down. A clean
session earns one per set, which is the whole target moving exactly as it
always has. A stall behind it buys fewer, and the sets that miss out stay where
they were:

| Sets | Missed before | Advance | `8,8,8,8` becomes |
| --- | --- | --- | --- |
| 4 | none | 4 | `9,9,9,9` |
| 4 | once | 3 | `9,9,9,8` |
| 4 | twice | 2 | `9,9,8,8` |
| 4 | three times or more | 1 | `9,8,8,8` |

A hit always earns at least one set, however long the stall, or there would be
no way out of one.

**The sets level up before the base moves.** From `9,9,8,8`, a clean session
goes to `9,9,9,9` rather than `10,10,9,9`: an uneven target is a way through a
stall rather than a shape to keep, so it closes at the first opportunity even
when the session earned more than it needed. Only once every set agrees again
does the figure itself go up.

Two things end a run of misses besides hitting the target:

- **A change of load.** A different weight is a different ladder, and its
  misses say nothing about this one. The session where you moved from 5 kg to
  10 kg is not a stall at 10 kg.
- **Running out of history.** An exercise added to the routine since, or
  trained too long ago to appear in the activity search, reads as no stall -
  the same answer as a first-ever session, and the right one when we cannot see
  far enough back to say otherwise.

Reading the misses means reading what past sessions were *asked* for, which is
not in the workout: `update` rewrote that after each of them. It comes from the
workout Garmin keeps beside each activity - what the watch actually ran. See
[Garmin's API](garmin-api.md#the-workout-an-activity-was-performed-against).

Only sessions that could still change the answer are fetched. A smoothly
progressing exercise settles after one, and the walk stops at `sets - 1` misses
because the advance is pinned at its minimum from there on.

## Deloading

Rule 4 repeats a missed target, which is right the first time: you may simply
have had a bad day, and the same weight goes up next session on better sleep.
It is wrong forever. **Missing the same target twice in a row is a stall**, and
something has to give.

Two things can, in this order:

1. **The rep range.** The target eases to where the session actually landed -
   at least one set easier, and no higher than what you managed. This is nearly
   free, which is why it goes first: a double progression's rep range exists to
   be spent, and the [granular advance](#coming-back-from-a-stall) climbs back
   through it gently.
2. **The load.** Only once the range is gone - `rep_low` on every set and still
   short - does weight come off, one `weight_step`, and the range is climbed
   again from the bottom.

Because easing has to bottom out in the range before the load moves, weight
does not come off until the third failure at the earliest. That is where
StrongLifts and Starting Strength land too, arrived at from the other side:
they wait three failures because they have no rep range to spend first.

A worked stall, squat 6-10 x 3 sets, 2.5 kg step:

| Session | Target | Performed | Next | Why |
| --- | --- | --- | --- | --- |
| 1 | `9` | 9,9,8 | `9` | First miss, bad day, repeat |
| 2 | `9` | 9,9,8 | `9,9,8` | Missed twice, ease one set |
| 3 | `9,9,8` | 9,9,8 | `9` | Hit it, levels up |

And a stall that runs out of range:

| Session | Target | Performed | Next | Why |
| --- | --- | --- | --- | --- |
| 1 | `6` x 20 | 5,5,5 | `6` x 20 | First miss, repeat |
| 2 | `6` x 20 | 5,5,5 | `6` x 17.5 | Nothing left in the range, -2.5 kg |

A bad miss skips the crawl: four reps short of a target of 10 eases straight to
where you are rather than spending four sessions stepping down to it. It never
eases below `rep_low`, since that is the bottom of what you programmed.

### Why not reset to the top of the range

The tempting mirror of rule 3 - drop a weight step, reset to `rep_high` - reads
as symmetric and behaves badly:

- **It bounces.** One good session at `rep_high` tops out the range, so rule 3
  hands the weight straight back and puts you at `rep_low` on the load that
  just failed. Fail, deload, succeed, fail, deload.
- **It is often harder than what it replaces.** `rep_high` at one step down
  only beats `rep_low` at the old weight when the step is larger than the whole
  rep range is worth, roughly `step/weight > (rep_high - rep_low)/(30 +
  rep_high)`. A 2.5 kg step on a 20 kg press over an 8-14 range is 12% against
  the 14% it needs, so the "deload" is a harder session. It also gets worse as
  you get stronger, because the step shrinks as a share of the load.

Climbing from `rep_low` instead makes the lighter load an accumulation block -
several sessions of real work - rather than a bounce.

### How light it can go

A deload stops at `min_weight`: the smallest bar on the rack, the lightest pair
of dumbbells, the top plate of a stack. Declared per load type in
[`settings.min_weights`](configuration.md#settings), or per exercise. Reaching
it is reported rather than silently held, because an exercise pinned at the
bottom needs a change this tool cannot make - a different variation, more
sleep, fewer sets.

Bodyweight exercises have no load to take off, so a stall there says so.

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
| 4 | Same weight, any set short, first miss | Repeat unchanged (rule 4) |
| 4b | Same weight, any set short, missed before | Ease the target, or take weight off at the bottom of the range |
| 5 | Floor at or above `rep_high` | `rep_low` at weight + step (rule 3) |
| 6 | Otherwise | Advance `sets - misses` of the sets (rule 2) |

Case 4 is judged set by set rather than against a single figure, since an
uneven target does not ask the same of all of them. It is counted rather than
matched in order - `8,9,9,8` against `9,9,8,8` is two nines and two eights,
which is what was asked - because the watch logs what you did, not which set
was meant to be the hard one.

Bodyweight exercises never reach case 5's weight increase; they target
`rep_high` and hold.

Case 6 caps at `rep_high`, so an off-step target cannot overshoot the range.
Together with case 2, a target can never leave the programmed range.

An uneven target is stored as **two repeat groups** rather than one - Garmin
repeats a single step identically and cannot say two things at once - which is
why an exercise on its way back from a stall shows up twice in Connect and on
the watch. It collapses back to one group as soon as the sets level up.

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

The same squat, coming back from a stall rather than progressing smoothly:

| Stored target | Performed | Missed before | Next target | Why |
| --- | --- | --- | --- | --- |
| 8 x 20 | 8,8,8,8 @ 20 | twice | `9,9,8,8` x 20 | Two sets earned, two held back |
| `9,9,8,8` x 20 | 9,9,8,8 @ 20 | none | 9 x 20 | Levelled up, flat again |
| `9,9,8,8` x 20 | 9,9,8,8 @ 20 | three times | `9,9,9,8` x 20 | One more set levelled |
| `9,9,8,8` x 20 | 8,8,8,8 @ 20 | - | `9,9,8,8` x 20 | The two nines were missed |
| `9,9,8,8` x 20 | 10,10,10,10 @ 20 | - | 6 x 22.5 | Topped the range regardless |

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

There is a third thing, which is what keeps a run repeatable: **the workout
each activity was performed against**, which Garmin keeps beside the activity.
The stored target is only what a session was aiming at until something moves
it, and `--apply` moves it - after which that activity is still the most recent
one and would be judged all over again, against the target it had just earned.
Every set would read as short of a figure nobody was aiming at, and a second
miss on the record is what deloading acts on, so running twice would walk
targets backwards.

So a session is only judged while the stored target is still the one it was
given. Once it is not, the session is spent, and the run says so:

```text
  Barbell Deadlift    9 x 35 kg  ->  9 x 35 kg  (up to date)
```

A target typed in by hand reads the same way, and gets the same answer for the
same reason: the session predates it, so it is not evidence about it, and what
you typed stands until the next session is trained against it.
