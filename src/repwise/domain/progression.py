"""The double progression rules.

Pure logic: given how an exercise is programmed, what it is currently set to,
what was actually performed, and how badly it had been stalling, decide the next
prescription. No file access, no network, no Garmin types -- which is why it is
fully testable offline.

The rules, as written in README.md:

1. Start at the lower end of the range, e.g. 6-6-6 for a range of 6-10.
2. Each workout, add a rep to every set, e.g. 7-7-7, then 8-8-8 -- but after a
   stall, to only some of them, so that the way back up is gentler than the way
   that failed.
3. Once all sets reach the upper end, increase the weight and reset to the
   lower end.
4. If you didn't match the previous result, stay at the same weight and reps.
5. A load is only adopted once it can be carried for the bottom of the range.
   Lift something other than what was prescribed and come up short of rep_low
   and the load was too heavy to keep, so the previous target stands.

No state file is needed: the target currently stored in the Garmin workout is
the "previous result" to match, the logged activity says what was actually
performed, and each past activity carries the workout as it was executed, which
is where a miss streak is read from.
"""

from collections import Counter
from dataclasses import dataclass, field

from .models import ExerciseSpec


@dataclass(frozen=True)
class Target:
    """A prescription: this many reps on every set, at this weight.

    `lead` is how many of the leading sets are asked for one `rep_step` *more*
    than `reps`, which is how a target recovers from a stall without demanding
    the full jump on every set at once. A flat target -- `lead` 0, the same
    figure everywhere -- is what an exercise progressing smoothly always holds,
    and the ramp closes again as soon as the sets have levelled up.
    """

    reps: int
    weight: float
    lead: int = 0

    def per_set(self, sets: int, rep_step: int = 1) -> list[int]:
        """What each set is asked for, hardest first."""
        higher = min(self.lead, sets)
        return [self.reps + rep_step] * higher + [self.reps] * (sets - higher)

    def spread(self, sets: int, rep_step: int = 1) -> str:
        """How to write this target: `8` when flat, `8+2` when it is ramped.

        `8+2` is eight reps on every set with two of them asked for a step
        more. Written that way rather than set by set - `9,9,8,8` - because
        those are the two numbers the rules move, and spelling every set out
        grows with the set count while saying nothing extra.

        A lead of every set is a flat target one step up, and is written as
        one: `per_set` asks all of them for the higher figure, so `8+3` across
        three sets would name three sets of eight that are not there. The rules
        never build one, but a workout whose Garmin steps hold more sets than
        the config asks for reads back as one.
        """
        ahead = min(self.lead, sets)
        if not ahead:
            return str(self.reps)
        if ahead >= sets:
            return str(self.reps + rep_step)
        return f"{self.reps}+{ahead}"


@dataclass(frozen=True)
class PerformedSet:
    """One working set as actually logged.

    For timed holds Garmin records repetitionCount 1 and puts the real figure
    in the duration, so `seconds` carries it and `as_time()` moves it into
    `reps` where the progression rules can use it.
    """

    reps: int
    weight: float = 0.0
    seconds: float = 0.0

    def as_time(self) -> PerformedSet:
        """Recast a timed hold so its seconds are the thing being progressed."""
        return PerformedSet(round(self.seconds), self.weight, self.seconds)


@dataclass(frozen=True)
class Session:
    """One past session of an exercise: what it asked for, and what was done.

    Assembled from a logged activity and the workout as that activity executed
    it, which is the only self-consistent pairing available: the workout stored
    in Garmin now holds the target for the *next* session, not the one any past
    activity was performed against.
    """

    target: Target
    performed: list[PerformedSet] = field(default_factory=list)


def working_weight(performed: list[PerformedSet]) -> float:
    """The load most of the sets were done at, ties going to the heavier one.

    A weight changed part-way through a session should not be averaged away,
    and the odd warm-up-ish first set at a lighter load should not drag the
    baseline back down.
    """
    counts = Counter(entry.weight for entry in performed)
    return max(counts, key=lambda weight: (counts[weight], weight))


def hit(spec: ExerciseSpec, target: Target, reps: list[int]) -> bool:
    """Whether a session met what was asked of it.

    Two conditions, which for a flat target collapse into the single one this
    tool has always applied: every set has to reach the base figure, and enough
    of them have to reach the higher one.

    The higher rung is counted rather than compared set by set, so the order
    the watch happened to log them in does not decide it. Against a target of
    `8+2`, 8,9,9,8 is two nines and two eights, which is what was asked.
    """
    if not reps:
        return False
    if min(reps) < target.reps:
        return False
    higher = target.reps + spec.rep_step
    return sum(1 for done in reps if done >= higher) >= target.lead


def miss_streak(spec: ExerciseSpec, history: list[Session], weight: float) -> int:
    """How many sessions in a row missed, immediately before the latest one.

    `history` is the sessions before the one being judged, newest first, and
    `weight` the load that one was worked at. The walk stops at:

    - the first session that hit, which is what ends a streak;
    - a change of load, because a different weight is a different ladder and
      its misses say nothing about this one;
    - `sets - 1` misses, past which the advance is already pinned at its
      minimum of one, so no amount of further history could change the answer.

    That last bound is what keeps the lookback short: a smoothly progressing
    exercise settles it after a single session.
    """
    limit = max(spec.sets - 1, 0)
    streak = 0

    for session in history:
        if streak >= limit:
            break
        if not session.performed or working_weight(session.performed) != weight:
            break
        at_weight = [
            entry.reps for entry in session.performed if entry.weight == weight
        ]
        if hit(spec, session.target, at_weight):
            break
        streak += 1

    return streak


#: How many sessions must have missed *before* this one for it to count as a
#: stall rather than a bad day. One, so the target eases on the second miss in
#: a row - and, because easing has to bottom out in the range before the load
#: moves, the weight does not come off until the third. That is the shape the
#: linear-progression programmes settled on, arrived at from the other side:
#: they wait three failures because they have no rep range to give first.
STALLED_AFTER = 1


def _ladder(spec: ExerciseSpec, target: Target) -> int:
    """Where a target sits on the ladder, so two of them can be compared.

    Rungs are counted rather than measured: `lead` is a fraction of a rep
    spread across the sets, so a plain rep count could not order `8+2` against
    `8+1`.
    """
    return target.reps * spec.sets + min(target.lead, spec.sets - 1)


def _one_rung_down(spec: ExerciseSpec, target: Target) -> Target:
    """The target one set easier: the exact inverse of a single advance.

    Without partial progression an advance is the whole target, so the inverse
    is too: the base comes down a step and every set comes with it. A ramp left
    over from before the setting changed loses its lead here along with the
    step, which is the one place this drops by more than a single rung. What
    bounds it is `_deload`, which never eases *above* what the session managed
    and never below `rep_low` - not that it stops at where the session landed,
    since a near miss is exactly the case that eases past it.
    """
    if not spec.partial_progression:
        return Target(target.reps - spec.rep_step, target.weight)
    if target.lead:
        return Target(target.reps, target.weight, target.lead - 1)
    return Target(target.reps - spec.rep_step, target.weight, spec.sets - 1)


def _achieved(spec: ExerciseSpec, weight: float, reps: list[int]) -> Target:
    """The session itself, written as the target it would have been.

    The base is what every set reached and the lead is how many beat it, which
    is the same reading `block_target` gives a workout. A set that beat it by
    more than one step is counted once: the ladder has no rung for the rest,
    and asking for less than was managed is the safe direction to round.
    """
    floor = min(reps)
    if not spec.partial_progression:
        # No rung between two flat targets to land on, so what the session
        # managed is what every set managed.
        return Target(floor, weight)
    higher = floor + spec.rep_step
    beat = sum(1 for done in reps if done >= higher)
    return Target(floor, weight, min(beat, spec.sets - 1))


def _missed(spec: ExerciseSpec, current: Target, floor: int) -> str:
    """Why a session that fell short leaves the target where it was.

    What was asked is not repeated here: the report shows it, unchanged, in
    the columns beside this. What the session actually managed is the part
    only this can say.
    """
    return f"missed target, {floor} on the worst set"


def _deload(
    spec: ExerciseSpec, current: Target, weight: float, reps: list[int]
) -> tuple[Target, str]:
    """Give something back, having now missed the same target twice.

    Reps first, load second. The rep range is what a double progression has to
    give, and spending it is nearly free: the target eases to where the session
    actually landed and climbs back from there. Only once the range is spent -
    `rep_low` on every set, and still short - is the load the only thing left,
    and then it comes off a step and the range is climbed again from the
    bottom.

    Deliberately *not* a reset to the top of the range at a lighter load. That
    reads as the mirror of rule 3 but behaves badly: one good session at
    `rep_high` earns the weight straight back under rule 3, returning to the
    target that just failed, and for any exercise whose step is small next to
    its rep range the lighter top of the range is harder than the heavier
    bottom it replaced. Climbing from `rep_low` is what makes the lighter load
    into an accumulation block rather than a bounce.
    """
    bottom = Target(spec.rep_low, weight)

    if _ladder(spec, current) <= _ladder(spec, bottom):
        stalled = "stalled at the bottom of the range"
        if spec.bodyweight or spec.weight_step <= 0:
            return current, f"{stalled}, and there is no load to take off"
        lighter = weight - spec.weight_step
        if lighter < spec.min_weight:
            return (
                current,
                f"{stalled}, already at the {spec.min_weight:g} kg minimum",
            )
        return Target(spec.rep_low, lighter), f"{stalled}, take a step off the load"

    # At least one rung down, and no higher than the session managed: a near
    # miss eases by one, and a bad miss drops straight to where you actually
    # are rather than spending sessions crawling down to it.
    down = _one_rung_down(spec, current)
    did = _achieved(spec, weight, reps)
    eased = down if _ladder(spec, down) <= _ladder(spec, did) else did
    if _ladder(spec, eased) < _ladder(spec, bottom):
        eased = bottom  # the range has a floor, and this is it

    # Where it eased *to* is in the report's own columns; how far down that is,
    # and why that far, is what has to be said here.
    if eased == bottom:
        return eased, "missed twice, ease to the bottom of the range"
    if eased == down:
        # With partial progression off there is no set to name: every set moved
        # together on the way up, and the whole target comes down together too.
        unit = "second" if spec.time_based else "rep"
        plural = "" if spec.rep_step == 1 else "s"
        by = (
            "ease by one set"
            if spec.partial_progression
            else f"take {spec.rep_step} {unit}{plural} off"
        )
        return eased, f"missed twice, {by}"
    return eased, "missed twice, ease to where the session landed"


def _advance(
    spec: ExerciseSpec,
    current: Target,
    weight: float,
    floor: int,
    streak: int,
) -> tuple[Target, str]:
    """Move a session that counted, by an amount the streak behind it decides.

    Split out from `next_target` so that the guards deciding *whether* a
    session moves the target stay readable apart from how far it moves.
    """
    rebased = weight != current.weight
    # The base is always what the weakest set actually did, which is what makes
    # a heavier load or a deload rebase onto itself rather than onto whatever
    # the workout still had stored.
    #
    # A part-levelled ramp only survives a session that landed exactly on it.
    # Clear its base on every set and the ramp has been overtaken -- nothing is
    # left part-done underneath a figure every set has already beaten -- and at
    # a different load it belonged to the old ladder rather than this one.
    base = floor
    lead = current.lead if not rebased and floor == current.reps else 0

    # Rule 3: topped out the range, so add load and reset to the bottom.
    if base >= spec.rep_high:
        if spec.bodyweight:
            return (
                Target(spec.rep_high, weight),
                "top of the range, and bodyweight, so nothing to add",
            )
        # The step goes on top of the load actually used, so a session at a
        # different weight moves the prescription by more than one step. Name
        # what was lifted, otherwise the jump looks arbitrary.
        lifted = f" at {weight:g} kg" if rebased else ""
        heavier = weight + spec.weight_step
        # `max_weight` is the heaviest load that exists to be prescribed, so a
        # step past it is shortened to land *on* it rather than refused. The
        # last pair on the rack is still a pair, and stopping short of a weight
        # you own would leave the top of your equipment permanently unused for
        # the sake of a step size the equipment never promised to divide into.
        #
        # Not the mirror of the deload's floor, deliberately. A short step up
        # is a smaller increase than usual, which is always safe to prescribe;
        # a short step *down* is a smaller decrease than usual, which may not
        # be enough to break the stall that asked for it. So the ceiling is
        # rounded to and the floor is refused.
        if spec.max_weight is not None and heavier > spec.max_weight:
            if weight >= spec.max_weight:
                # Nothing left to add. The target settles at the top of the
                # range, which is where bodyweight work already lives: once
                # the load has run out, the range is all there is to progress.
                # "past" rather than "at" for a load above the ceiling, which
                # is what a target set before the ceiling was declared leaves
                # behind. Rule 3 does not pull it down - taking load off is the
                # deload's job - so the report has to be able to say so.
                sits = "already at" if weight == spec.max_weight else "past"
                return (
                    Target(spec.rep_high, weight),
                    f"top of the range, {sits} the {spec.max_weight:g} kg maximum",
                )
            return (
                Target(spec.rep_low, spec.max_weight),
                f"hit {floor} on every set{lifted}, top of the range, "
                f"up to the {spec.max_weight:g} kg maximum",
            )
        return (
            Target(spec.rep_low, heavier),
            f"hit {floor} on every set{lifted}, top of the range",
        )

    # Rule 2, one set at a time. A unit is a `rep_step` on a single set, filled
    # from the first set down: a clean session earns one per set, which is the
    # whole target moving as it always has, and every miss in the streak behind
    # it costs one of them. A hit always earns at least one, however long the
    # streak, or a stall could never end.
    # Unless partial progression is off, in which case the streak buys nothing:
    # every set moves together, so a hit is always the whole target moving.
    units = spec.sets if not spec.partial_progression else max(spec.sets - streak, 1)
    if lead:
        # Level the sets before the base moves. The ramp is a way through a
        # stall rather than a shape to keep, so it closes at the first
        # opportunity even when the session earned more than it needed.
        units = min(units, spec.sets - lead)
    lead += units

    # What each of these adds is a reason the report cannot read off its own
    # columns: which load the target now belongs to, that the session beat what
    # it was asked for, and that a stall is being climbed out of.
    moved = ", rebased on the load you used" if rebased else ""
    beat = ""
    if not rebased and floor > current.reps:
        beat = f"beat target ({floor} on every set), "
    stalled = f"hit after {streak} miss{'es' if streak > 1 else ''}, " if streak else ""
    # A timed hold is progressed in seconds -- `as_time` put them where the
    # reps go -- so the step has to be named in the unit the exercise is
    # actually measured in. "add 1 rep" beside a 25 s -> 26 s target names
    # something the exercise does not have.
    unit = "second" if spec.time_based else "rep"
    plural = "" if spec.rep_step == 1 else "s"

    if lead >= spec.sets:
        # Every set is asked for the same figure again, which is a flat target
        # one step up. Capped, so an off-step target cannot overshoot the range.
        reps = min(base + spec.rep_step, spec.rep_high)
        return (
            Target(reps, weight),
            f"{beat}{stalled}add {spec.rep_step} {unit}{plural}{moved}",
        )

    return (
        Target(base, weight, lead),
        f"{beat}{stalled}add {spec.rep_step} {unit}{plural} on "
        f"{units} of {spec.sets} sets{moved}",
    )


def next_target(
    spec: ExerciseSpec,
    current: Target,
    performed: list[PerformedSet],
    streak: int = 0,
) -> tuple[Target, str]:
    """Decide the next prescription for one exercise.

    `streak` is how many sessions in a row missed before this one, from
    `miss_streak`, and only narrows how far a session that counted moves the
    target. It defaults to none, which is the smoothly-progressing case and the
    behaviour this tool had before granular progression existed.

    Returns the new target plus a short human-readable reason.
    """
    if not performed:
        return current, "no sets logged"

    # Judge everything at the load actually used, which may not be the load the
    # workout still has stored.
    weight = working_weight(performed)
    at_weight = [entry.reps for entry in performed if entry.weight == weight]
    rebased = weight != current.weight

    # Progress from the weakest set, not from the stored target. The next
    # target has to be achievable on every set, so extra reps on the easy sets
    # earn nothing while the floor stays put -- but beating the target on all
    # of them does count.
    floor = min(at_weight)

    # A set carried at a *heavier* load, for at least the reps the working sets
    # managed, counts as one of them: harder than asked is not worse than
    # asked, and a session split across two loads is still a whole session.
    # Without this the set count is taken from one load alone, and three sets
    # done as two at 20 kg and one at 30 kg read as an abandoned session rather
    # than a finished one.
    #
    # What it contributes is its own rep count, which is a lower bound on what
    # it would have managed at the lighter load, so this can only ever add sets
    # to the tally - never flatter the reps. A set that came up short at the
    # heavier load counts for nothing, which is what keeps a failed top set
    # from reading as a completed session.
    counted = at_weight + [
        entry.reps
        for entry in performed
        if entry.weight > weight and entry.reps >= floor
    ]

    # Rule 5: a load is only worth keeping once it can be carried for at least
    # the bottom of the range. Falling short of rep_low means the jump was too
    # big -- the 3 kg dumbbells were taken, so you grabbed the 4 kg ones --
    # and rebasing onto it would prescribe a rep count outside the programmed
    # range. Only rebased loads are judged here; at an unchanged load rule 4
    # already covers it, since a stored target never sits below rep_low.
    if rebased and floor < spec.rep_low:
        return (
            current,
            f"only {floor} at {weight:g} kg, below the "
            f"{spec.rep_low}-{spec.rep_high} range",
        )

    # Too few sets that counted to judge progression, so bank the load and
    # repeat. Rule 4 still applies when the load itself did not change. The
    # ramp does not survive this: half a session cannot say which sets levelled.
    if len(counted) < spec.sets:
        reps = floor if rebased else current.reps
        return (
            Target(reps, weight),
            f"only {len(counted)} of {spec.sets} sets logged, consolidate",
        )

    # Rule 4: every prescribed set must meet what was asked of it to count as a
    # match. Only meaningful while the load is unchanged.
    if not rebased and not hit(spec, current, counted):
        if streak >= STALLED_AFTER:
            return _deload(spec, current, weight, counted)
        return current, _missed(spec, current, floor)

    return _advance(spec, current, weight, floor, streak)
