"""Compare a config against what Garmin actually holds.

Config and Garmin drift: an exercise gets swapped in the Garmin app, a set
count changes, or a `garmin_name` was copied from an activity rather than from
the workout. Matching falls back to the category, so drift can go unnoticed
until the fallback stops working too.

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
    """Something about a workout that does not line up."""

    workout: str
    detail: str
    severity: str = "warning"

    def __str__(self) -> str:
        return f"{self.workout}: {self.detail}"


def check_workout(workout: Workout, payload: dict) -> list[Finding]:
    """Compare one configured workout against its Garmin definition."""
    findings: list[Finding] = []

    def note(detail: str, severity: str = "warning") -> None:
        findings.append(Finding(workout.key, detail, severity))

    blocks = list(iter_exercise_blocks(payload))
    index: ExerciseIndex[ExerciseBlock] = ExerciseIndex()
    for entry in blocks:
        index.add(
            entry,
            name=step_exercise_name(entry.step),
            category=step_category(entry.step),
        )

    seen = set()
    for spec in workout.exercises:
        # Only the name here, not the full lookup: falling back to the category
        # silently is exactly the drift this command exists to report.
        block = index.by_name(spec.garmin_name)

        if block is None:
            candidates = index.claiming(spec.garmin_category)
            if len(candidates) == 1:
                block = candidates[0]
                actual = step_exercise_name(block.step)
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
                continue
            else:
                note(
                    f"{spec.name}: {spec.garmin_name} is not in the Garmin "
                    f"workout at all",
                    "error",
                )
                continue

        seen.add(id(block))

        if block.sets != spec.sets:
            note(f"{spec.name}: {spec.sets} sets in config, {block.sets} in Garmin")
        if spec.rest and block.rest is None:
            # Nothing to correct automatically: `update` can retime a rest, but
            # not turn a lap.button one into a countdown. A note either way,
            # since the workout still runs.
            note(
                f"{spec.name}: rest {spec.rest}s in config, but Garmin waits "
                f"for the lap button; only you can change that",
                "note",
            )
        elif block.rest is not None and spec.rest and block.rest != spec.rest:
            note(
                f"{spec.name}: rest {spec.rest}s in config, {block.rest}s in "
                f"Garmin (update --apply will set it)",
                "note",
            )

    for block in blocks:
        if id(block) in seen:
            continue
        label = step_exercise_name(block.step) or step_category(block.step)
        note(f"{label} is in the Garmin workout but not in the config")

    return findings
