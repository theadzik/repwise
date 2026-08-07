"""What a prescription really costs, once your body is counted as load.

Progression works entirely in the units Garmin stores: reps, and the weight on
the bar or the stack. That is the right currency for deciding a target, and
nothing in `progression.py` needs any other. It is the wrong currency for
judging whether the *programming* makes sense, because one large part of the
load is not in it: **your body**. A standing calf raise moves all of you plus
the stack, and a weighted pull-up all of you plus the belt.
`bodyweight_factor` is the share of you that the movement carries.

Get it wrong and rule 3 misreads badly. A calf raise topping out at 20 reps
and gaining 5 kg looks like a 25% jump on a 20 kg stack; with 80 kg of lifter
on top it is a 5% jump, nowhere near enough to pay for the reset from 20 reps
to 12, and the "weight increase" is really a large step backwards.

It is never inferred. A lat pull-down is categorised `PULL_UP` and carries none
of your bodyweight, so guessing from the category would be wrong on the first
exercise that needed it. The default of zero means "the stored weight is the
load", which is what every barbell, cable and dumbbell movement wants.

Everything else about the load stays a matter of what you type into the watch.
An exercise carrying a pair of dumbbells is entered as the weight of the pair,
with a `weight_step` to match, and needs nothing here: 2 x 5 kg stepping by
1 kg is 10 kg stepping by 2, which is what the watch should be holding anyway.

Nothing here decides anything. It is read by `check` and reported; targets are
computed from the stored weight exactly as they always were.
"""

from dataclasses import replace

from .models import ExerciseSpec

#: Epley's divisor: one rep is worth roughly 1/30 of the load. Used only to
#: compare two prescriptions of the *same* exercise, where much of the error in
#: the estimate is common to both sides and cancels. The implied one-rep max is
#: never reported, which matters because Epley is known to drift above about
#: twelve reps and calf work lives well past that.
EPLEY_REPS = 30.0

#: How far rule 3 may shift the effort, in *either* direction, before `check`
#: mentions it.
#:
#: Some shift is inherent to double progression: topping out the range and
#: climbing it again from `rep_low` gives back part of what the load just
#: gained, and a well-programmed barbell lift still hands back a few percent
#: every cycle. A strict "never moves" test would fire on nearly every exercise
#: and mean nothing. Ten percent is where the ordinary sawtooth stops and a
#: mismatch between the range and the step begins.
#:
#: One threshold for both signs, which is a deliberate simplification rather
#: than a claim that they are equally bad. A range too wide only wastes
#: sessions re-treading ground; a range too narrow puts a wall in front of
#: every weight jump, and rule 5 refusing the load turns that into a loop -
#: climb the range, fail the jump, deload, climb it again. The narrow side
#: therefore deserves to fire sooner, and if it ever proves too quiet the fix
#: is to split this constant in two rather than to move it.
TOLERATED_SHIFT = 0.10


def effective_load(spec: ExerciseSpec, weight: float, bodyweight: float = 0.0) -> float:
    """The kilograms actually moved, given the weight Garmin has stored."""
    return weight + bodyweight * spec.bodyweight_factor


def e1rm(load: float, reps: float) -> float:
    """Epley's estimate, in whatever unit `load` came in."""
    return load * (1.0 + reps / EPLEY_REPS)


def worked_reps(spec: ExerciseSpec, reps: int) -> float:
    """Reps as the trained muscle did them, not as the watch counted them.

    Alternating work is programmed in the watch's units - both sides counted,
    `rep_step` set to the number of sides, which is what [alternating
    exercises](../../../docs/progression.md) prescribes - so a 16-24 range is
    really 8-12 for the leg doing the work. Feeding the doubled figure to
    Epley overstates how much a reset costs, by enough to invent a finding on
    an exercise that is programmed perfectly well.

    For everything else `rep_step` is 1 and this changes nothing.
    """
    return reps / spec.rep_step


def reset_drop(
    spec: ExerciseSpec, weight: float, bodyweight: float = 0.0
) -> float | None:
    """How much easier rule 3's weight jump leaves the exercise, as a fraction.

    Rule 3 fires at the top of the range: `rep_high` at `weight` becomes
    `rep_low` at `weight + weight_step`. This compares the two as effort, and
    the sign is the whole diagnosis - both directions are defects, and they are
    the same quantity:

    - **Positive**, the reset gives back more than the load gained. The range is
      too wide for the step, and every weight increase is a step backwards
      spent re-treading ground.
    - **Negative**, the load gained more than the reset gives back. The range is
      too narrow for the step, and every weight increase is a wall.

    Neither can be seen by reading the range; both need the load, and for a
    calf raise or a lunge that means needing the lifter too.

    `None` where the question does not arise: a bodyweight or timed exercise
    never reaches rule 3, and neither does one with no step or no load.
    """
    if spec.bodyweight or spec.time_based or spec.weight_step <= 0:
        return None

    topped = effective_load(spec, weight, bodyweight)
    if topped <= 0:
        return None
    reset = topped + spec.weight_step

    return 1.0 - e1rm(reset, worked_reps(spec, spec.rep_low)) / e1rm(
        topped, worked_reps(spec, spec.rep_high)
    )


#: The highest `rep_high` worth suggesting. Past this a rep range has stopped
#: being strength programming, and Epley - already drifting by twelve reps - is
#: far enough outside its band that a wider suggestion would be arithmetic
#: rather than advice. A 1 kg step on a 1 kg dumbbell is a 100% jump that no
#: rep range absorbs; the honest answer there is to micro-load, and refusing to
#: suggest `12-47` is how this says so.
_HIGHEST_USEFUL_REPS = 30


def fitting_rep_high(
    spec: ExerciseSpec,
    weight: float,
    bodyweight: float = 0.0,
    tolerance: float = TOLERATED_SHIFT,
) -> int | None:
    """The nearest `rep_high` whose reset lands within `tolerance`, either way.

    A range too wide is narrowed and one too narrow is widened. Both
    directions are tried, but only one of them can ever hold the answer:
    `reset_drop` rises monotonically with `rep_high`, so narrowing a range that
    is already too narrow only makes it worse. That is what lets a single
    ordered walk serve both cases without first asking which one it is in.

    The first fit is returned, so the answer is the smallest edit that works
    rather than the most comfortable one - a suggestion you can argue with is
    more use than one that rewrites the exercise.

    Searched with `reset_drop` rather than solved for in closed form. The
    algebra is easy enough to invert, but then the rule would be written twice
    and the two could disagree - which they do, at exactly the boundary case a
    suggestion lands on, where floating point puts the "safe" answer a hair
    over the line. One definition, consulted, cannot drift from itself.

    Counted in whole `rep_step`s, so the range suggested is one the exercise
    can actually climb: a 16-24 range stepping by 2 must move to 22 or 26,
    never 21, or the ladder would straddle `rep_high` and never land on it to
    earn the weight jump at all.

    `None` when nothing between `rep_low` and `_HIGHEST_USEFUL_REPS` fits,
    which means the step itself is the thing to change.
    """
    down = range(spec.rep_high - spec.rep_step, spec.rep_low, -spec.rep_step)
    up = range(spec.rep_high + spec.rep_step, _HIGHEST_USEFUL_REPS + 1, spec.rep_step)

    for candidate in [*down, *up]:
        shift = reset_drop(replace(spec, rep_high=candidate), weight, bodyweight)
        if shift is not None and abs(shift) <= tolerance:
            return candidate
    return None
