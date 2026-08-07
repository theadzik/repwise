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

#: How much easier rule 3 may make an exercise before `check` mentions it.
#:
#: Some drop is inherent to double progression: topping out the range and
#: climbing it again from `rep_low` gives back part of what the load just
#: gained, and a well-programmed barbell lift still hands back a few percent
#: every cycle. A strict "never easier" test would therefore fire on nearly
#: every exercise and mean nothing. Ten percent is where the ordinary sawtooth
#: stops and a rep range too wide for its real step begins.
TOLERATED_DROP = 0.10


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
    `rep_low` at `weight + weight_step`. This compares the two as effort, so a
    positive result means the load went up and the session got *easier* - which
    is the failure mode a wide range on a small real step produces, and the one
    thing about a range that cannot be seen by reading it.

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


def widest_rep_high(
    spec: ExerciseSpec,
    weight: float,
    bodyweight: float = 0.0,
    tolerance: float = TOLERATED_DROP,
) -> int | None:
    """The largest `rep_high` this exercise's real step can pay for.

    Searched with `reset_drop` rather than solved for in closed form. The
    algebra is easy enough to invert, but then the rule would be written twice
    and the two could disagree - which they do, at exactly the boundary case a
    suggestion lands on, where floating point puts the "safe" answer a
    hair over the line. One definition, consulted, cannot drift from itself.

    Ranges are a handful of reps wide, so the search costs nothing.

    Counted down in whole `rep_step`s, so the range suggested is one the
    exercise can actually climb: a 16-24 range stepping by 2 must be narrowed
    to 22 or 20, never 21, or the ladder would straddle `rep_high` and never
    land on it to earn the weight jump at all.

    `None` when even one step above `rep_low` is too wide. At the default
    tolerance that does not happen - a rep is worth about 1/(30 + `rep_low`),
    well inside it - so it means the caller asked for a stricter one.
    """
    for candidate in range(spec.rep_high - spec.rep_step, spec.rep_low, -spec.rep_step):
        trial = replace(spec, rep_high=candidate)
        drop = reset_drop(trial, weight, bodyweight)
        if drop is not None and drop <= tolerance:
            return candidate
    return None
