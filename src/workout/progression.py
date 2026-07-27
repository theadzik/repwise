"""The double progression rules.

Pure logic: given how an exercise is programmed, what it is currently set to,
and what was actually performed, decide the next prescription. No file access,
no network, no Garmin types -- which is why it is fully testable offline.

The rules, as written in README.md:

1. Start at the lower end of the range, e.g. 6-6-6 for a range of 6-10.
2. Each workout, add a rep to every set, e.g. 7-7-7, then 8-8-8.
3. Once all sets reach the upper end, increase the weight and reset to the
   lower end.
4. If you didn't match the previous result, stay at the same weight and reps.

No state file is needed: the target currently stored in the Garmin workout is
the "previous result" to match, and the logged activity says what was actually
performed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .models import ExerciseSpec


@dataclass(frozen=True)
class Target:
    """A prescription: this many reps on every set, at this weight."""

    reps: int
    weight: float


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


def working_weight(performed: list[PerformedSet]) -> float:
    """The load most of the sets were done at, ties going to the heavier one.

    A weight changed part-way through a session should not be averaged away,
    and the odd warm-up-ish first set at a lighter load should not drag the
    baseline back down.
    """
    counts = Counter(entry.weight for entry in performed)
    return max(counts, key=lambda weight: (counts[weight], weight))


def next_target(
    spec: ExerciseSpec,
    current: Target,
    performed: list[PerformedSet],
) -> tuple[Target, str]:
    """Decide the next prescription for one exercise.

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

    # Too few sets at that load to judge progression, so bank the load and
    # repeat. Rule 4 still applies when the load itself did not change.
    if len(at_weight) < spec.sets:
        reps = floor if rebased else current.reps
        return (
            Target(reps, weight),
            f"only {len(at_weight)}/{spec.sets} sets at {weight:g} kg, consolidate",
        )

    # Rule 4: every prescribed set must meet the target to count as a match.
    # Only meaningful while the load is unchanged.
    if not rebased and floor < current.reps:
        return current, f"missed target ({floor}/{current.reps} on worst set), repeat"

    # Rule 3: topped out the range, so add load and reset to the bottom.
    if floor >= spec.rep_high:
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

    # Rule 2: otherwise add a rep to every set. Exercises counted per side step
    # by 2 so both sides advance together, capped so the range is not overshot.
    moved = f" at {weight:g} kg" if rebased else ""
    reps = min(floor + spec.rep_step, spec.rep_high)
    plural = "" if spec.rep_step == 1 else "s"

    # The step is taken from what was performed, not from the stored target, so
    # overshooting the target moves the prescription by more than one step. Say
    # where the new baseline came from, otherwise the jump looks arbitrary.
    beat = ""
    if not rebased and floor > current.reps:
        beat = f"beat target ({floor} on every set vs {current.reps}), "
    return (
        Target(reps, weight),
        f"{beat}add {spec.rep_step} rep{plural} ({floor} -> {reps}){moved}",
    )
