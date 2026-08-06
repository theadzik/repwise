"""Is the config talking about the exercises it thinks it is?

One question, and not the one `update` answers. `update --dry-run` already
says what would change and then changes it; anything it can fix is not drift to
report. What it cannot fix is a `garmin_name` that no longer names anything, or
names something only by luck - and since the config now drives the workout,
that mistake is expensive: the exercise Garmin holds goes unnamed and is
removed, taking the target stored in it, while a new step is built beside it.

So this checks identity, and leaves sets, rests and the exercise list to the
command that owns them.

Pure: takes a config and payloads, returns findings.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain.matching import ExerciseIndex
from .domain.models import Workout
from .garmin.payloads import (
    ExerciseBlock,
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

    def __str__(self) -> str:
        return f"{self.workout}: {self.detail}"


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
