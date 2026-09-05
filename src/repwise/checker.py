"""Three questions `update` does not answer.

`update --dry-run` already says what would change and then changes it; anything
it can fix is not drift to report. What is left is what it cannot:

**Existence.** A `garmin_name` or `garmin_category` that Garmin has never heard
of. This is the prior question to the one below, and its consequences are
worse: an exercise Garmin does not recognise cannot be built at all, so the
step never reaches the watch no matter how often you sync.

**Identity.** A `garmin_name` that no longer names anything, or names something
only by luck. Since the config drives the workout, that mistake is expensive:
the exercise Garmin holds goes unnamed and is removed, taking the target stored
in it, while a new step is built beside it.

**Programming.** A rep range that does not fit its weight step - too wide and
every weight increase is a step backwards, too narrow and each one is a wall -
both of which `update` carries out faithfully forever, because every individual
decision it makes is correct. Only the shape of the range is wrong, and that is
visible only once the load is counted properly - see `domain/effort.py`.

Sets, rests and the exercise list are left to the command that owns them.

Pure: takes a config and payloads, returns findings.
"""

from dataclasses import dataclass, replace

from .domain.effort import (
    TOLERATED_SHIFT,
    chosen_step,
    effective_load,
    fitting_rep_highs,
    reset_drop,
)
from .domain.matching import ExerciseIndex
from .domain.models import ExerciseSpec, Workout
from .garmin.catalog import ExerciseCatalog
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


def _unknown(spec: ExerciseSpec, catalog: ExerciseCatalog) -> str:
    """No such exercise anywhere in the catalog, under any category."""
    detail = f"{spec.name}: {spec.garmin_name} is not an exercise Garmin has"
    if near := catalog.like(spec.garmin_name):
        return f"{detail}. Did you mean {' or '.join(near)}?"
    if spec.garmin_category and not catalog.has_category(spec.garmin_category):
        # Both halves wrong usually means the pair was invented rather than
        # mistyped, so saying only the name is wrong would send you looking
        # for a spelling that was never the problem.
        return (
            f"{spec.name}: neither {spec.garmin_name} nor the category "
            f"{spec.garmin_category} is one Garmin has"
        )
    return f"{detail}, so `update` could not build a step for it"


def _corrected(spec: ExerciseSpec, found: list[tuple[str, str]]) -> str:
    """The name is real, but not under the category the config pairs it with.

    Three ways to be wrong, and the fix differs each time, so the wording does
    too: the spelling, the category, or the whole pair.
    """
    spellings = {name for _, name in found}
    categories = sorted({category for category, _ in found})

    if spec.garmin_category in categories:
        # Right shelf, wrong label. The only thing to change is the name.
        exact = next(name for cat, name in found if cat == spec.garmin_category)
        return f"{spec.name}: Garmin spells {spec.garmin_name} as {exact}"

    where = " or ".join(categories)
    if spellings == {spec.garmin_name}:
        return (
            f"{spec.name}: {spec.garmin_name} is filed under {where}, not "
            f"{spec.garmin_category}. Garmin checks the pair, so set "
            f"garmin_category: {categories[0]}"
        )

    category, exact = found[0]
    return (
        f"{spec.name}: Garmin has no {spec.garmin_category}/{spec.garmin_name}; "
        f"what it has is {category}/{exact}"
    )


def check_catalog(workout: Workout, catalog: ExerciseCatalog) -> list[Finding]:
    """Look for exercises Garmin has never heard of.

    `check_workout` below asks whether the config names what a Garmin *workout*
    holds. This asks the prior question - whether it names anything at all -
    and so is the only check here worth running against a workout Garmin does
    not have yet, which is exactly when it pays: the names are wrong before the
    workout is built rather than after.

    Everything found is an error. Garmin validates the category and the name
    against each other, so none of it is a matter of taste and none of it is
    something `update` could carry out anyway.
    """
    findings: list[Finding] = []

    def note(detail: str) -> None:
        findings.append(Finding(workout.key, detail, "error"))

    for spec in workout.exercises:
        category = spec.garmin_category
        if category is not None and catalog.holds(category, spec.garmin_name):
            continue

        found = catalog.locate(spec.garmin_name)
        if not found:
            note(_unknown(spec, catalog))
        elif category is not None:
            note(_corrected(spec, found))
        elif spec.garmin_name not in {name for _, name in found}:
            # No category to contradict, so the spelling is the whole question.
            # A missing category is a legitimate choice - matching falls back to
            # it, and an exercise that never needs the fallback need not declare
            # one - so its absence is not itself worth reporting.
            note(f"{spec.name}: Garmin spells {spec.garmin_name} as {found[0][1]}")

    return findings


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
                f"{actual}. They share category {spec.garmin_category} but are "
                f"different exercises, so `update` would rebuild the step and "
                f"restart its progression",
                "error",
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


def _suggestion(spec: ExerciseSpec, weight: float, bodyweight: float) -> str:
    """What to write instead, and how much room there is to argue with it.

    The whole window, not just the one number, because the tolerance is a band
    and a single figure hides how wide it is: knowing that 12-16 through 12-25
    all work is what lets you round to something you would actually count to,
    or decide the exercise is close enough to leave alone. `balanced` leads
    because a top that breaks even today drifts the least as you get stronger.

    Only the top is ever offered. `rep_low` is a decision about how heavy the
    exercise is allowed to get, and no arithmetic here is entitled to it - see
    `fitting_rep_highs`.
    """
    fitted = fitting_rep_highs(spec, weight, bodyweight)
    if fitted is None:
        # No range absorbs this step, so naming one would be arithmetic
        # dressed up as advice.
        return f"change weight_step from {spec.weight_step:g} kg"

    fix = f"make it {spec.rep_low}-{fitted.balanced}"
    if fitted.narrowest == fitted.widest:
        return fix
    return (
        f"{fix}; anything from {spec.rep_low}-{fitted.narrowest} to "
        f"{spec.rep_low}-{fitted.widest} fits"
    )


def check_programming(
    workout: Workout, payload: dict, bodyweight: float | None = None
) -> list[Finding]:
    """Look for rep ranges that do not fit what their weight step is worth.

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
        # Judge the step rule 3 will actually take. Where the equipment offers
        # a choice of increments, that is the one `chosen_step` picks for this
        # weight, not the smallest the load type happens to name - reporting
        # the 1.25 kg micro-plate as a wall on a 60 kg stack would be a finding
        # about a jump the tool was never going to prescribe.
        stepped = replace(
            spec,
            weight_step=chosen_step(spec, target.weight, carried),
            tiers=(),
        )
        shift = reset_drop(stepped, target.weight, carried)
        if shift is None or abs(shift) <= TOLERATED_SHIFT:
            continue

        load = effective_load(stepped, target.weight, carried)
        # The sign is the whole diagnosis, so it decides every word that
        # follows: which way the range is wrong, which way to move it, and
        # what it costs you to leave it alone.
        if shift > 0:
            gives, costs = "gives back more", f"{shift:.0%} drop in effort"
            settle = "accept the sawtooth"
        else:
            gives, costs = "gives back less", f"{-shift:.0%} jump in effort"
            settle = "micro-load"
        fix = _suggestion(stepped, target.weight, carried)
        findings.append(
            Finding(
                workout.key,
                f"{spec.name}: +{stepped.weight_step:g} kg on {load:g} kg is "
                f"{stepped.weight_step / load:.1%}, but resetting "
                f"{spec.rep_high}->{spec.rep_low} reps {gives}, so the weight "
                f"increase is a {costs} ({fix}, or {settle})",
            )
        )

    return findings
