"""Two questions `update` does not answer.

`update --dry-run` already says what would change and then changes it; anything
it can fix is not drift to report. What is left is what it cannot:

**Identity.** A `garmin_name` that no longer names anything, or names something
only by luck. Since the config drives the workout, that mistake is expensive:
the exercise Garmin holds goes unnamed and is removed, taking the target stored
in it, while a new step is built beside it.

**Programming.** A rep range too wide for what its weight step is really worth,
which `update` will carry out faithfully forever because every individual
decision it makes is correct. Only the shape of the range is wrong, and that is
visible only once the load is counted properly - see `domain/effort.py`.

Sets, rests and the exercise list are left to the command that owns them.

Pure: takes a config and payloads, returns findings.
"""

from dataclasses import dataclass

from .domain.effort import (
    TOLERATED_DROP,
    effective_load,
    reset_drop,
    widest_rep_high,
)
from .domain.matching import ExerciseIndex
from .domain.models import Workout
from .garmin.payloads import (
    ExerciseBlock,
    block_target,
    iter_exercise_blocks,
    step_category,
    step_exercise_name,
)


@dataclass(frozen=True)
class Finding:
    """Something about a workout that does not line up.

    Everything reported is worth fixing by hand, which is what lets `check`
    exit non-zero on any finding at all and mean something by it.
    """

    workout: str
    detail: str
    severity: str = "warning"


def check_workout(workout: Workout, payload: dict) -> list[Finding]:
    """Look for exercises the config cannot name properly."""
    findings: list[Finding] = []

    def note(detail: str, severity: str = "warning") -> None:
        findings.append(Finding(workout.key, detail, severity))

    index: ExerciseIndex[ExerciseBlock] = ExerciseIndex()
    for entry in iter_exercise_blocks(payload):
        index.add(
            entry,
            name=step_exercise_name(entry.step),
            category=step_category(entry.step),
        )

    for spec in workout.exercises:
        # Only the name here, not the full lookup: falling back to the category
        # silently is exactly the drift this command exists to report.
        if index.by_name(spec.garmin_name) is not None:
            continue

        candidates = index.claiming(spec.garmin_category)
        if len(candidates) == 1:
            actual = step_exercise_name(candidates[0].step)
            note(
                f"{spec.name}: config says {spec.garmin_name}, Garmin says "
                f"{actual}. Matched by category {spec.garmin_category}, so "
                f"it works, but the name is wrong"
            )
        elif len(candidates) > 1:
            note(
                f"{spec.name}: {spec.garmin_name} not in Garmin, and "
                f"category {spec.garmin_category} is ambiguous there",
                "error",
            )
        else:
            note(
                f"{spec.name}: {spec.garmin_name} is not in the Garmin "
                f"workout at all, so `update` would build a new step for it "
                f"and drop the one Garmin has",
                "error",
            )

    return findings


def check_programming(
    workout: Workout, payload: dict, bodyweight: float | None = None
) -> list[Finding]:
    """Look for rep ranges too wide for what their weight step is really worth.

    Judged at the weight the exercise is actually loaded to today, read out of
    the Garmin workout rather than guessed from the config, because the answer
    moves as you get stronger: a step is a shrinking share of the load, so a
    range that was fine at 20 kg can stop being fine at 40 kg. That is the
    point of checking it every run rather than once when it is written.

    Exercises Garmin does not hold are skipped in silence - `check_workout`
    reports those, and reporting them twice for different reasons would only
    bury its answer.
    """
    findings: list[Finding] = []

    index: ExerciseIndex[ExerciseBlock] = ExerciseIndex()
    for entry in iter_exercise_blocks(payload):
        index.add(
            entry,
            name=step_exercise_name(entry.step),
            category=step_category(entry.step),
        )

    for spec in workout.exercises:
        if spec.bodyweight_factor and bodyweight is None:
            findings.append(
                Finding(
                    workout.key,
                    f"{spec.name}: carries {spec.bodyweight_factor:g} of your "
                    f"bodyweight, but no weigh-in was found and "
                    f"settings.bodyweight is unset, so its range was not checked",
                )
            )
            continue

        block = index.find(spec.garmin_name, spec.garmin_category)
        if block is None:
            continue
        target = block_target(block, spec)
        if target is None or target.weight <= 0:
            continue

        carried = bodyweight or 0.0
        drop = reset_drop(spec, target.weight, carried)
        if drop is None or drop <= TOLERATED_DROP:
            continue

        load = effective_load(spec, target.weight, carried)
        widest = widest_rep_high(spec, target.weight, carried)
        # Say what to change, not only that something is wrong. Narrowing the
        # range is nearly always the practical fix: the step is a property of
        # the rack, and making it bigger is the option you do not have.
        fix = (
            f"narrow to {spec.rep_low}-{widest}"
            if widest
            else "raise weight_step, or widen the gap another way"
        )
        findings.append(
            Finding(
                workout.key,
                f"{spec.name}: +{spec.weight_step:g} kg on {load:g} kg is "
                f"{spec.weight_step / load:.1%}, "
                f"but resetting {spec.rep_high}->{spec.rep_low} reps needs more, "
                f"so the weight increase is a {drop:.0%} drop in effort "
                f"({fix}, or accept the sawtooth)",
            )
        )

    return findings
