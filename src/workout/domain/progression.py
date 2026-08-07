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

from __future__ import annotations

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
        """How to write this target: `8` when flat, `9,9,8,8` when ramped."""
        if not self.lead:
            return str(self.reps)
        return ",".join(str(reps) for reps in self.per_set(sets, rep_step))


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
    the watch happened to log them in does not decide it. Against a 9,9,8,8
    target, 8,9,9,8 is two nines and two eights, which is what was asked.
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
                "at top of range (bodyweight, add load or hold)",
            )
        # The step goes on top of the load actually used, so a session at a
        # different weight moves the prescription by more than one step. Name
        # what was planned, otherwise the jump looks arbitrary.
        lifted = f" at {weight:g} kg (planned {current.weight:g} kg)" if rebased else ""
        return (
            Target(spec.rep_low, weight + spec.weight_step),
            f"hit {floor} on every set{lifted}, +{spec.weight_step:g} kg "
            f"and reset to {spec.rep_low}",
        )

    # Rule 2, one set at a time. A unit is a `rep_step` on a single set, filled
    # from the first set down: a clean session earns one per set, which is the
    # whole target moving as it always has, and every miss in the streak behind
    # it costs one of them. A hit always earns at least one, however long the
    # streak, or a stall could never end.
    units = max(spec.sets - streak, 1)
    if lead:
        # Level the sets before the base moves. The ramp is a way through a
        # stall rather than a shape to keep, so it closes at the first
        # opportunity even when the session earned more than it needed.
        units = min(units, spec.sets - lead)
    lead += units

    moved = f" at {weight:g} kg" if rebased else ""
    # The step is taken from what was performed, not from the stored target, so
    # overshooting the target moves the prescription by more than one step. Say
    # where the new baseline came from, otherwise the jump looks arbitrary.
    beat = ""
    if not rebased and floor > current.reps:
        beat = f"beat target ({floor} on every set vs {current.reps}), "
    stalled = f"hit after {streak} miss{'es' if streak > 1 else ''}, " if streak else ""

    if lead >= spec.sets:
        # Every set is asked for the same figure again, which is a flat target
        # one step up. Capped, so an off-step target cannot overshoot the range.
        reps = min(base + spec.rep_step, spec.rep_high)
        new = Target(reps, weight)
        plural = "" if spec.rep_step == 1 else "s"
        return (
            new,
            f"{beat}{stalled}add {spec.rep_step} rep{plural} "
            f"({current.spread(spec.sets, spec.rep_step)} -> "
            f"{new.spread(spec.sets, spec.rep_step)}){moved}",
        )

    new = Target(base, weight, lead)
    plural = "" if spec.rep_step == 1 else "s"
    return (
        new,
        f"{beat}{stalled}add {spec.rep_step} rep{plural} on {units} of "
        f"{spec.sets} sets ({current.spread(spec.sets, spec.rep_step)} -> "
        f"{new.spread(spec.sets, spec.rep_step)}){moved}",
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
        return current, "no sets logged, target unchanged"

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
            f"{spec.rep_low}-{spec.rep_high} range, keep "
            f"{current.reps} x {current.weight:g} kg",
        )

    # Too few sets at that load to judge progression, so bank the load and
    # repeat. Rule 4 still applies when the load itself did not change. The
    # ramp does not survive this: half a session cannot say which sets levelled.
    if len(at_weight) < spec.sets:
        reps = floor if rebased else current.reps
        return (
            Target(reps, weight),
            f"only {len(at_weight)}/{spec.sets} sets at {weight:g} kg, consolidate",
        )

    # Rule 4: every prescribed set must meet what was asked of it to count as a
    # match. Only meaningful while the load is unchanged.
    if not rebased and not hit(spec, current, at_weight):
        asked = current.spread(spec.sets, spec.rep_step)
        if current.lead:
            return current, f"missed target ({floor} on worst set vs {asked}), repeat"
        return current, f"missed target ({floor}/{current.reps} on worst set), repeat"

    return _advance(spec, current, weight, floor, streak)
