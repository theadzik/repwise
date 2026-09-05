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

from dataclasses import dataclass, replace

from .models import ExerciseSpec, LoadTier

#: Epley's divisor: one rep is worth roughly 1/30 of the load. Used only to
#: compare two prescriptions of the *same* exercise, where much of the error in
#: the estimate is common to both sides and cancels. The implied one-rep max is
#: never reported, which matters because Epley is known to drift above about
#: twelve reps and calf work lives well past that.
EPLEY_REPS = 30.0

#: How much easier rule 3's reset may leave an exercise before `check` says so.
#:
#: The permissive side, and deliberately so. A positive shift means the range
#: hands back more than the weight jump took, which costs sessions re-treading
#: ground and nothing else: the sets are still carried to the same proximity to
#: failure, and hypertrophy is [equivalent across a wide span of
#: loads](https://pmc.ncbi.nlm.nih.gov/articles/PMC7927075/) when they are. It
#: is a bill paid in time, not in adaptation.
#:
#: Fifteen percent because a smaller figure convicts the guidance itself. ACSM's
#: [progression position stand](https://pubmed.ncbi.nlm.nih.gov/19204579/) asks
#: for a "2-10% (lower percent for small muscle mass exercises, higher percent
#: increase for large muscle mass exercises) increase in load", and the low end
#: of that - the end it names for small muscle mass, which is most of what gets
#: a wide range - lands at +14.3% on an ordinary 12-20. A threshold under that
#: reports a lateral raise progressed exactly as recommended.
#:
#: There is a ceiling on this side that the other does not have: with no step at
#: all the shift is `(rep_high - rep_low) / (30 + rep_high)`, so what is really
#: being measured is a range too wide to be paid for. No range narrower than
#: about eight reps can reach 15% however small the step, which is the intended
#: reading - past that width the range is the thing to change, and inside it the
#: step always can be.
TOLERATED_SAWTOOTH = 0.15

#: How much *harder* rule 3's reset may leave an exercise before `check` says so.
#:
#: The strict side. A negative shift means the load gained more than the range
#: gives back, so the set straight after the jump is heavier than anything the
#: last cycle asked for - and rule 5 refuses a load that cannot be carried for
#: `rep_low`, which turns it into a loop: climb the range, fail the jump,
#: deload, climb it again. That is progress stopped, not progress slowed.
#:
#: Seven and a half percent: a third tighter than the sawtooth side allows in
#: absolute terms and half what it was when one figure served both, but not so
#: tight that it fires inside its own error bars.
#:
#: Two floors set it. Day-to-day 1RM reliability has a [median CV of about
#: 4.2%](https://academicworks.cuny.edu/cgi/viewcontent.cgi?article=1338&context=le_pubs),
#: so a reset harder by less than that is one a good day absorbs and no advice
#: is owed. And `reset_drop` is an Epley estimate on both sides, drifting past
#: twelve reps by its own admission above, which puts a few more percent of
#: slack under any reading. A threshold at 5% sits on top of both: an ordinary
#: 6-10 squat taking 5 kg on 30 kg scores exactly -5.0%, and calling that a
#: defect would be reporting the arithmetic's own noise as a finding.
#:
#: What is left past 7.5% is a jump no rep range in the config absorbs - the
#: 1 kg step on a 2 kg dumbbell, the 2.5 kg step on a 2.5 kg one - where rule 5
#: refuses the load and the exercise loops: climb the range, fail the jump,
#: deload, climb it again. That is progress stopped rather than slowed, which is
#: why this side is the strict one.
TOLERATED_WALL = 0.075


def within_tolerance(shift: float) -> bool:
    """Whether a reset this far from break-even is worth reporting.

    The one place the asymmetry lives. Both signs are defects and they are the
    same quantity, but they are not the same size of problem, so they do not
    get the same threshold.
    """
    return -TOLERATED_WALL <= shift <= TOLERATED_SAWTOOTH


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
#: rep range absorbs; the honest answer there is a smaller step, and refusing to
#: suggest `12-47` is how this says so.
_HIGHEST_USEFUL_REPS = 30


@dataclass(frozen=True)
class RepHighs:
    """Where the top of a rep range could sit, at the load it sits at today.

    A window rather than an answer, because there is more than one: the
    tolerance is a band, and a suggestion that names one number hides how much
    room there is to round it off, or to leave the exercise where it is. The
    tops in it are contiguous - `reset_drop` rises monotonically with
    `rep_high` - so the two ends describe the whole of it.
    """

    #: The one whose reset comes nearest to paying for the weight jump exactly.
    balanced: int
    #: The tightest and loosest tops still inside the tolerance.
    narrowest: int
    widest: int


def fitting_rep_highs(
    spec: ExerciseSpec,
    weight: float,
    bodyweight: float = 0.0,
    sawtooth: float = TOLERATED_SAWTOOTH,
    wall: float = TOLERATED_WALL,
) -> RepHighs | None:
    """Every `rep_high` whose reset is tolerable, and the best one.

    A range too wide is narrowed and one too narrow is widened, both by moving
    the same end. **`rep_low` is never suggested**, and that is a statement
    about what the two ends mean rather than a limitation of the search:

    - `rep_low` is the only rep count that says how hard the exercise ever
      gets. The set straight after a weight jump - `rep_low` reps at the new
      weight - is the highest relative intensity in the cycle, so moving it
      down to make the arithmetic work means training a joint heavier than you
      chose to. It is a judgement about the exercise, and it is yours.
    - `rep_high` decides nothing except when the jump has been earned, and it
      is a function of how strong you are today: the step is a shrinking share
      of a growing load, so the top wants to come down as you progress while
      the bottom does not move at all.

    Solving for the bottom would also fight itself. The break-even top is
    roughly `rep_low + (30 + rep_low) x step / load`, so raising `rep_low` to
    narrow a range widens what the range needs to be - a smaller edit in one
    direction and a larger requirement in the other.

    Searched with `reset_drop` rather than solved in closed form. The algebra
    is easy enough to invert, but then the rule would be written twice and the
    two could disagree - which they do, at exactly the boundary case a
    suggestion lands on, where floating point puts the "safe" answer a hair
    over the line. One definition, consulted, cannot drift from itself.

    Counted in whole `rep_step`s, so every top offered is one the exercise can
    actually climb to: a 16-24 range stepping by 2 must move to 22 or 26, never
    21, or the ladder would straddle `rep_high` and never land on it to earn
    the weight jump at all.

    `None` when nothing between `rep_low` and `_HIGHEST_USEFUL_REPS` fits,
    which means the step itself is the thing to change.
    """
    fits: list[tuple[float, int]] = []
    for candidate in range(
        spec.rep_low + spec.rep_step, _HIGHEST_USEFUL_REPS + 1, spec.rep_step
    ):
        shift = reset_drop(replace(spec, rep_high=candidate), weight, bodyweight)
        if shift is not None and -wall <= shift <= sawtooth:
            fits.append((abs(shift), candidate))

    if not fits:
        return None
    return RepHighs(min(fits)[1], fits[0][1], fits[-1][1])


def chosen_step(spec: ExerciseSpec, weight: float, bodyweight: float = 0.0) -> float:
    """Which of a tier's increments to add at this weight.

    A tier naming one step has nothing to choose and returns it. Where it names
    several - a stack that takes 1.25 kg plates as well as pin moves - the right
    one depends on how heavy the load already is, which is the whole reason the
    choice cannot be made in the config file: 2.5 kg on a 5 kg stack is a wall,
    and on a 60 kg stack it is beneath noticing.

    The rule is the largest step whose jump `reset_drop` still calls tolerable.
    Reading it in the two directions the drop already means:

    - A step too big for the load leaves a large *negative* drop - the range
      cannot give back what the weight took - so it is refused, and a lighter
      one is tried.
    - A step too small leaves a large *positive* drop, the range handing back
      more than the load gained. Those are tolerable but wasteful, so taking
      the largest that fits walks the step up as the load grows.

    Where nothing fits - a band so narrow that every step is a wall - the least
    bad is returned rather than nothing, because rule 3 has to prescribe
    something and refusing to progress is worse than progressing roughly.
    """
    tier = spec.tier_for(weight)
    steps = tier.steps or (spec.weight_step,)
    if len(steps) == 1:
        return steps[0]

    best: tuple[float, float] | None = None
    for step in sorted(steps, reverse=True):
        drop = reset_drop(replace(spec, weight_step=step, tiers=()), weight, bodyweight)
        if drop is None:
            continue
        if within_tolerance(drop):
            return step
        if best is None or abs(drop) < best[1]:
            best = (step, abs(drop))
    return best[0] if best else steps[0]


def _rungs_above(tier: LoadTier, weight: float, step: float) -> float | None:
    """The lightest load this tier can express that is heavier than `weight`.

    Counted from the tier's own floor rather than from `weight`, so a load that
    is not on this rack's grid - carried over from another rack, or typed into
    the watch by hand - lands back on it rather than dragging the offset along
    for good.
    """
    if step <= 0:
        return None
    if weight < tier.minimum:
        return tier.minimum
    rungs = int((weight - tier.minimum) / step) + 1
    landed = tier.minimum + rungs * step
    if tier.maximum is not None and landed > tier.maximum:
        # The last pair on the rack is still a pair: a step past the ceiling is
        # shortened to land on it, exactly as rule 3 has always done.
        return tier.maximum if weight < tier.maximum else None
    return landed


def next_weight_above(
    spec: ExerciseSpec, weight: float, bodyweight: float = 0.0
) -> float | None:
    """The next load up, crossing onto the next rack where this one ends.

    `None` is the end of the equipment: the heaviest tier at its ceiling, with
    nothing above it to move to.
    """
    span = spec.tier_span
    tier = spec.tier_for(weight)
    landed = _rungs_above(tier, weight, chosen_step(spec, weight, bodyweight))
    if landed is not None:
        return landed
    heavier = [nxt for nxt in span if nxt.minimum > tier.minimum]
    if not heavier:
        return None
    # Crossing racks lands on the lightest load the next one can express that
    # is heavier than where we are. Its own floor, normally - the two racks
    # meet at a gap, 10 kg to 12 - but a rack that starts below where you
    # already are is walked up its grid instead of stepping backwards.
    nxt = heavier[0]
    if nxt.minimum > weight:
        return nxt.minimum
    return _rungs_above(nxt, weight, chosen_step(spec, nxt.minimum, bodyweight))


def next_weight_below(
    spec: ExerciseSpec, weight: float, bodyweight: float = 0.0
) -> float | None:
    """The next load down, dropping onto the rack below where this one starts.

    `None` is the bottom of the equipment. A deload that reaches it stays put:
    there is no lighter weight to prescribe, which is what `min_weight` has
    always meant.
    """
    span = spec.tier_span
    tier = spec.tier_for(weight)
    step = chosen_step(spec, weight, bodyweight)
    if step > 0 and weight - step >= tier.minimum:
        return weight - step
    lighter = [prev for prev in span if prev.minimum < tier.minimum]
    if not lighter:
        return None
    prev = lighter[-1]
    if prev.maximum is not None and prev.maximum < weight:
        return prev.maximum
    below = _rungs_above(
        prev, weight - step, chosen_step(spec, prev.minimum, bodyweight)
    )
    return below if below is not None and below < weight else prev.minimum
