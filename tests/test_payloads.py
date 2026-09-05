"""Mapping Garmin's JSON to and from this application's types."""

from dataclasses import replace

from builders import (
    GARMIN_GROUP_KEYS,
    GARMIN_STEP_KEYS,
    active,
    rep_step,
    rest_step,
    spec,
    timed_rest,
    workout,
)

from repwise.domain.progression import Target
from repwise.garmin.payloads import (
    GENERATED_NOTE,
    apply_block,
    apply_note,
    apply_rest,
    apply_target,
    block_target,
    is_timed_rest,
    iter_exercise_blocks,
    new_group,
    new_rest,
    new_workout,
    performed_sets,
    renumber,
    set_exercise_steps,
    step_note,
    step_rest,
    step_target,
)

# --- walking the structure ------------------------------------------------


def test_iter_descends_into_repeat_groups():
    """Sets are a RepeatGroupDTO wrapping the exercise plus a rest step."""
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 4,
            "workoutSteps": [
                rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0),
                timed_rest(90.0),
            ],
        }
    )
    blocks = list(iter_exercise_blocks(payload))

    assert len(blocks) == 1, "the rest step is not an exercise of its own"
    assert blocks[0].step["exerciseName"] == "BARBELL_BACK_SQUAT"
    assert blocks[0].sets == 4
    assert blocks[0].rest == 90


def test_a_step_outside_a_repeat_group_is_one_set_and_no_rest():
    payload = workout(rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0))
    block = next(iter(iter_exercise_blocks(payload)))

    assert (block.sets, block.rest, block.rest_step) == (1, None, None)


def test_a_zero_second_rest_is_a_duration_like_any_other():
    """Falsy but present: a step that can hold a rest, currently holding none."""
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 3,
            "workoutSteps": [
                rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0),
                timed_rest(0.0),
            ],
        }
    )
    block = next(iter(iter_exercise_blocks(payload)))

    assert block.rest == 0
    assert block.rest_step is not None, "a configured rest can be written here"


def test_a_timed_rest_with_no_value_reads_as_no_rest():
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 3,
            "workoutSteps": [
                rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0),
                {
                    "stepType": {"stepTypeKey": "rest"},
                    "endCondition": {"conditionTypeKey": "time"},
                    "endConditionValue": None,
                },
            ],
        }
    )
    assert next(iter(iter_exercise_blocks(payload))).rest_step is None


def test_a_lap_button_rest_is_no_interval_at_all():
    """It prompts you to press the button; the value beside it means nothing."""
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 3,
            "workoutSteps": [rep_step("PLANK", "PLANK", 30, None), rest_step(60.0)],
        }
    )
    block = next(iter(iter_exercise_blocks(payload)))

    assert block.rest is None
    assert block.rest_step is None, "nothing to write a configured rest onto"


# --- reading targets ------------------------------------------------------


def test_workout_step_weight_is_kilograms_not_grams():
    """weightValue 30.0 with a kilogram unit is 30 kg, not 0.03 kg."""
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0)
    assert step_target(step) == Target(6, 30.0)


def test_gram_unit_is_converted():
    gram = {"unitId": 1, "unitKey": "gram", "factor": 1.0}
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 20000.0, unit=gram)
    assert step_target(step) == Target(6, 20.0)


def test_missing_unit_is_assumed_kilograms():
    step = rep_step("STANDING_CALF_RAISE", "CALF_RAISE", 12, 20.0, unit=None)
    assert step_target(step) == Target(12, 20.0)


def test_missing_weight_reads_as_zero():
    step = rep_step("STANDING_CALF_RAISE", "CALF_RAISE", 12, None)
    assert step_target(step) == Target(12, 0.0)


def test_rest_steps_have_no_target():
    assert step_target(rest_step()) is None


def test_timed_step_needs_the_time_flag():
    step = {
        "exerciseName": "PLANK",
        "category": "PLANK",
        "endCondition": {"conditionTypeKey": "time"},
        "endConditionValue": 47.0,
    }
    assert step_target(step) is None, "a timed hold is not a rep target"
    assert step_target(step, time_based=True) == Target(47, 0.0)


# --- writing targets ------------------------------------------------------


def test_apply_round_trips_through_the_unit():
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0)
    apply_target(step, Target(8, 32.5))
    assert step_target(step) == Target(8, 32.5)


def test_apply_adds_a_unit_when_the_step_has_none():
    step = rep_step("STANDING_CALF_RAISE", "CALF_RAISE", 12, None, unit=None)
    apply_target(step, Target(12, 20.0))
    assert step["weightUnit"]["unitKey"] == "kilogram"
    assert step_target(step) == Target(12, 20.0)


def test_apply_rest_retimes_a_countdown():
    step = timed_rest(90.0)
    apply_rest(step, 150)

    assert step_rest(step) == 150
    assert is_timed_rest(step)


def test_apply_rest_turns_a_lap_button_wait_into_a_countdown():
    """Writing "rest this long" onto a step that waits for a button press has
    to change the condition as well as the number. Whether that is a change
    worth making is the planner's call, not this module's."""
    step = rest_step(60.0)
    assert not is_timed_rest(step)

    apply_rest(step, 45)

    assert is_timed_rest(step)
    assert step_rest(step) == 45


def test_a_written_rest_reads_back_through_the_block():
    """What the planner relies on to leave an already-correct rest alone."""
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 3,
            "workoutSteps": [
                rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0),
                timed_rest(90.0),
            ],
        }
    )
    block = next(iter(iter_exercise_blocks(payload)))
    assert block.rest_step is not None
    apply_rest(block.rest_step, 120)

    assert block.rest == 120


def test_apply_leaves_bodyweight_unloaded():
    step = {
        "exerciseName": "PLANK",
        "category": "PLANK",
        "endCondition": {"conditionTypeKey": "time"},
        "endConditionValue": 47.0,
    }
    apply_target(step, Target(48, 0.0))
    assert step["endConditionValue"] == 48.0
    assert "weightValue" not in step


# --- notes ----------------------------------------------------------------
#
# Garmin calls this field `description` on the step; Connect labels it "Notes"
# and the watch reads it as WorkoutStepInfo.notes. Verified by round-tripping
# a value through update_workout against a real account.


def test_note_is_absent_null_and_empty_alike():
    assert step_note({}) == ""
    assert step_note({"description": None}) == ""
    assert step_note({"description": ""}) == ""


def test_apply_note_writes_the_description_field():
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0)
    apply_note(step, "6-10 reps | +5 kg")
    assert step["description"] == "6-10 reps | +5 kg"
    assert step_note(step) == "6-10 reps | +5 kg"


def test_note_does_not_disturb_the_target():
    """Notes and targets live in different fields and must not interfere."""
    step = rep_step("BARBELL_BACK_SQUAT", "SQUAT", 6, 30.0)
    apply_note(step, "6-10 reps | +5 kg")
    assert step_target(step) == Target(6, 30.0)


def test_every_rendered_note_is_recognised_as_generated():
    """Whatever ExerciseSpec.note produces must match the pattern that
    decides a note is safe to overwrite, or the tool would refuse to update
    its own notes."""
    cases = [
        spec(),  # barbell reps
        spec(rep_step=2, rep_low=16, rep_high=24),  # per-side step
        spec(load="bodyweight", weight_step=0.0),  # bodyweight
        spec(load="bodyweight", weight_step=0.0, unit="seconds"),  # timed hold
        spec(load="dumbbell", weight_step=1.0),  # fractional step
        # and the same again carrying a cue, which is what the note looks like
        # for most of a real config and what this invariant is easiest to lose
        spec(notes="2-3 RIR | brace, knees out"),
        spec(rep_step=2, rep_low=16, rep_high=24, notes="0-1 RIR | hips square"),
        spec(load="bodyweight", weight_step=0.0, notes="0-1 RIR | body in one line"),
        spec(
            load="bodyweight",
            weight_step=0.0,
            unit="seconds",
            notes="stop when the hip drops",
        ),
        spec(load="cable", weight_step=2.5),
    ]
    for each in cases:
        assert GENERATED_NOTE.match(each.note), each.note


def test_a_hand_written_note_is_not_mistaken_for_a_generated_one():
    for text in [
        "elbows tucked",
        "6-10 reps",  # truncated, missing the load half
        "6-10 reps | +5 kg  elbows in",  # ours plus a hand-added cue
        "keep 6-10 reps | +5 kg",
    ]:
        assert not GENERATED_NOTE.match(text), text


# --- building a workout ---------------------------------------------------


SQUAT = spec(sets=3, rest=120, weight_step=2.5)
PLANK = spec(
    name="Plank",
    garmin_name="PLANK",
    garmin_category="PLANK",
    rep_low=30,
    rep_high=60,
    sets=2,
    load="bodyweight",
    weight_step=0.0,
    unit="seconds",
)


def built(*specs, between=None):
    """A whole workout built from specs, each starting at the bottom of its
    range, laid out with the same gap between every exercise."""
    payload = new_workout("Workout C")
    groups = [[new_group(s, Target(s.rep_low, s.start_weight))] for s in specs]
    gaps = [new_rest(between) for _ in range(len(specs) - 1)]
    set_exercise_steps(payload, groups, gaps)
    return payload


def orders(payload):
    """Every stepOrder and childStepId, depth first, as Garmin would see them."""
    found = []
    for step in payload["workoutSegments"][0]["workoutSteps"]:
        found.append((step["stepOrder"], step["childStepId"]))
        for inner in step.get("workoutSteps") or []:
            found.append((inner["stepOrder"], inner["childStepId"]))
    return found


def test_a_built_workout_reads_back_as_the_exercises_it_was_built_from():
    """The two halves of this module have to agree, or nothing else can."""
    payload = built(SQUAT, PLANK)
    blocks = list(iter_exercise_blocks(payload))

    assert [b.step["exerciseName"] for b in blocks] == ["BARBELL_BACK_SQUAT", "PLANK"]
    assert [b.sets for b in blocks] == [3, 2]
    assert [b.rest for b in blocks] == [120, None]
    assert step_target(blocks[0].step) == Target(6, 0.0)
    assert step_target(blocks[1].step, time_based=True) == Target(30, 0.0)


def test_a_built_exercise_carries_its_note():
    payload = built(SQUAT)
    step = next(iter(iter_exercise_blocks(payload))).step
    assert step_note(step) == SQUAT.note


def test_a_start_weight_is_where_a_built_exercise_begins():
    payload = built(replace(SQUAT, start_weight=40.0))
    step = next(iter(iter_exercise_blocks(payload))).step
    assert step_target(step) == Target(6, 40.0)


def test_an_exercise_with_no_rest_configured_still_gets_a_rest_step():
    """A lap-button rest, the way Connect builds one. A step that exists can be
    given a duration later; one that does not have to be inserted."""
    group = built(replace(SQUAT, rest=0))["workoutSegments"][0]["workoutSteps"][0]
    _, rest = group["workoutSteps"]

    assert rest["endCondition"]["conditionTypeKey"] == "lap.button"
    assert rest["endConditionValue"] is None


def test_nothing_follows_the_last_exercise():
    """A workout ends when its last set does."""
    steps = built(SQUAT, PLANK, between=60)["workoutSegments"][0]["workoutSteps"]

    kinds = [s["stepType"]["stepTypeKey"] for s in steps]
    assert kinds == ["repeat", "rest", "repeat"], "one gap, and only between"


def test_the_gap_between_exercises_is_configurable():
    steps = built(SQUAT, PLANK, between=45)["workoutSegments"][0]["workoutSteps"]
    assert steps[1]["endConditionValue"] == 45.0
    assert steps[1]["endCondition"]["conditionTypeKey"] == "time"


def test_no_gap_configured_is_a_wait_for_the_lap_button():
    steps = built(SQUAT, PLANK)["workoutSegments"][0]["workoutSteps"]
    assert steps[1]["endCondition"]["conditionTypeKey"] == "lap.button"


def test_numbering_is_flat_and_depth_first():
    """Verified against a real account: this is what Garmin renumbers to, and
    matching it is what stops every run finding a difference to write."""
    assert orders(built(SQUAT, PLANK, between=60)) == [
        (1, 1),  # squat group
        (2, 1),  # the squat
        (3, 1),  # its rest between sets
        (4, None),  # the gap, outside any group
        (5, 2),  # plank group
        (6, 2),
        (7, 2),
    ]


def test_renumbering_is_idempotent():
    """A second run must find the payload exactly as it left it."""
    payload = built(SQUAT, PLANK, between=60)
    before = orders(payload)

    renumber(payload)

    assert orders(payload) == before


def test_renumbering_reorders_by_rewriting_the_numbers():
    """Garmin sorts by stepOrder, so moving an exercise is renumbering it."""
    payload = built(SQUAT, PLANK, between=60)
    steps = payload["workoutSegments"][0]["workoutSteps"]
    payload["workoutSegments"][0]["workoutSteps"] = [steps[2], steps[1], steps[0]]

    renumber(payload)

    names = [b.step["exerciseName"] for b in iter_exercise_blocks(payload)]
    assert names == ["PLANK", "BARBELL_BACK_SQUAT"]
    assert orders(payload) == [
        (1, 1),
        (2, 1),
        (3, 1),
        (4, None),
        (5, 2),
        (6, 2),
        (7, 2),
    ]


def test_a_built_workout_uses_only_fields_garmin_knows():
    """A misspelt field would be accepted and silently ignored, so every key
    written is checked against the ones a real payload came back with."""
    for group in built(SQUAT, PLANK, between=60)["workoutSegments"][0]["workoutSteps"]:
        keys = set(group)
        if group["type"] == "RepeatGroupDTO":
            assert keys <= GARMIN_GROUP_KEYS, keys - GARMIN_GROUP_KEYS
            for inner in group["workoutSteps"]:
                assert set(inner) <= GARMIN_STEP_KEYS, set(inner) - GARMIN_STEP_KEYS
        else:
            assert keys <= GARMIN_STEP_KEYS, keys - GARMIN_STEP_KEYS


def test_a_built_workout_has_no_id_of_its_own():
    """The id is Garmin's to issue, and its absence is what says 'create me'."""
    assert "workoutId" not in new_workout("Workout C")


# --- reading performed sets -----------------------------------------------


def test_activity_weight_is_grams():
    by_name, _ = performed_sets(
        {"exerciseSets": [active("SQUAT", "SQUAT", 9, 20000.0)]}
    )
    assert by_name["squat"][0].weight == 20.0


def test_an_unrecorded_weight_reads_as_none_at_all():
    """Garmin's -1 means 'no figure', not a gram below nothing.

    Read literally it becomes -0.001 kg, which is a load like any other as far
    as the rules are concerned: the session rebases onto it and the next target
    is prescribed at a negative weight.
    """
    by_name, _ = performed_sets({"exerciseSets": [active("SQUAT", "SQUAT", 9, -1.0)]})
    assert by_name["squat"][0].weight == 0.0


def test_rest_sets_are_skipped():
    payload = {
        "exerciseSets": [
            active("SQUAT", "SQUAT", 9, 20000.0),
            {"setType": "REST", "repetitionCount": 0, "exercises": []},
        ]
    }
    by_name, _ = performed_sets(payload)
    assert len(by_name["squat"]) == 1


def test_sets_are_indexed_by_category_too():
    """Garmin can log a null name; the category still identifies it."""
    payload = {
        "exerciseSets": [
            {
                "setType": "ACTIVE",
                "repetitionCount": 11,
                "weight": 10000.0,
                "exercises": [{"name": None, "category": "TRICEPS_EXTENSION"}],
            }
        ]
    }
    by_name, by_category = performed_sets(payload)
    assert by_name == {}
    assert by_category["tricepsextension"][0].reps == 11


def test_duration_is_kept_for_timed_holds():
    payload = {"exerciseSets": [active("PLANK", "PLANK", 1, 0.0, duration=46.0)]}
    by_name, _ = performed_sets(payload)
    assert by_name["plank"][0].as_time().reps == 46


# --- an exercise split across two groups ----------------------------------
#
# A repeat group repeats one step identically, so a target that asks more of
# the leading sets than of the rest needs two of them, side by side. Above this
# module that is still one exercise.

RAMP_SQUAT = spec(sets=4)


def ramped(high_sets=2, high=9, low_sets=2, low=8, name="BARBELL_BACK_SQUAT"):
    """The two adjacent groups this tool writes a ramped target as."""
    return workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": high_sets,
            "workoutSteps": [rep_step(name, "SQUAT", high, 30.0), timed_rest(120.0)],
        },
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": low_sets,
            "workoutSteps": [rep_step(name, "SQUAT", low, 30.0), timed_rest(120.0)],
        },
    )


def test_two_adjacent_groups_of_one_exercise_read_as_one_block():
    blocks = list(iter_exercise_blocks(ramped()))

    assert len(blocks) == 1, "one exercise, however many groups hold it"
    assert blocks[0].sets == 4, "the two counts added together"
    assert len(blocks[0].groups) == 2


def test_a_split_exercise_reads_back_as_the_ramp_it_is():
    block = next(iter(iter_exercise_blocks(ramped())))
    assert block_target(block, RAMP_SQUAT) == Target(8, 30.0, lead=2)


def test_a_ramp_reads_the_same_whichever_half_comes_first():
    """Reordered by hand in Connect, it is still two nines and two eights."""
    reordered = ramped(high_sets=2, high=8, low_sets=2, low=9)
    block = next(iter(iter_exercise_blocks(reordered)))
    assert block_target(block, RAMP_SQUAT) == Target(8, 30.0, lead=2)


def test_two_groups_more_than_a_step_apart_are_not_a_ramp():
    """Not a shape this tool writes, so it reads as its base and is collapsed."""
    block = next(iter(iter_exercise_blocks(ramped(high=12, low=8))))
    assert block_target(block, RAMP_SQUAT) == Target(8, 30.0, lead=0)


def test_two_groups_of_different_exercises_stay_separate():
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 2,
            "workoutSteps": [rep_step("BARBELL_BACK_SQUAT", "SQUAT", 9, 30.0)],
        },
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 2,
            "workoutSteps": [rep_step("BARBELL_BENCH_PRESS", "BENCH_PRESS", 8, 30.0)],
        },
    )
    assert len(list(iter_exercise_blocks(payload))) == 2


def test_writing_a_ramp_splits_one_group_into_two():
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 4,
            "workoutSteps": [
                rep_step("BARBELL_BACK_SQUAT", "SQUAT", 8, 30.0),
                timed_rest(120.0),
            ],
        }
    )
    block = next(iter(iter_exercise_blocks(payload)))
    groups = apply_block(block, RAMP_SQUAT, Target(8, 30.0, lead=2))

    assert [g["numberOfIterations"] for g in groups] == [2, 2]
    assert [g["workoutSteps"][0]["endConditionValue"] for g in groups] == [9.0, 8.0]


def test_writing_a_flat_target_collapses_a_ramp_back_to_one_group():
    block = next(iter(iter_exercise_blocks(ramped())))
    groups = apply_block(block, RAMP_SQUAT, Target(9, 30.0))

    assert len(groups) == 1
    assert groups[0]["numberOfIterations"] == 4
    assert groups[0]["workoutSteps"][0]["endConditionValue"] == 9.0


def test_splitting_reuses_the_group_garmin_already_holds():
    """The first half keeps its identity, so its ids and history survive."""
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 4,
            "stepId": 12345,
            "workoutSteps": [rep_step("BARBELL_BACK_SQUAT", "SQUAT", 8, 30.0)],
        }
    )
    block = next(iter(iter_exercise_blocks(payload)))
    groups = apply_block(block, RAMP_SQUAT, Target(8, 30.0, lead=2))

    assert groups[0] is block.groups[0]
    assert groups[0]["stepId"] == 12345


def test_a_ramp_survives_being_written_and_read_back():
    payload = workout(
        {
            "type": "RepeatGroupDTO",
            "numberOfIterations": 4,
            "workoutSteps": [
                rep_step("BARBELL_BACK_SQUAT", "SQUAT", 8, 30.0),
                timed_rest(120.0),
            ],
        }
    )
    block = next(iter(iter_exercise_blocks(payload)))
    wanted = Target(8, 30.0, lead=2)
    groups = apply_block(block, RAMP_SQUAT, wanted)

    set_exercise_steps(payload, [groups], [])
    read_back = next(iter(iter_exercise_blocks(payload)))
    assert block_target(read_back, RAMP_SQUAT) == wanted
    assert read_back.sets == 4


def test_the_two_halves_of_an_exercise_sit_side_by_side():
    """No rest between exercises between them: the group's own rest covers it."""
    payload = new_workout("Workout C")
    squat = spec(sets=4)
    block = next(iter(iter_exercise_blocks(ramped())))
    halves = apply_block(block, squat, Target(8, 30.0, lead=2))
    other = [new_group(PLANK, Target(30, 0.0))]

    set_exercise_steps(payload, [halves, other], [new_rest(60)])

    steps = payload["workoutSegments"][0]["workoutSteps"]
    kinds = ["group" if step.get("workoutSteps") else "gap" for step in steps]
    assert kinds == ["group", "group", "gap", "group"]


def test_a_split_exercise_is_numbered_as_two_groups():
    payload = new_workout("Workout C")
    block = next(iter(iter_exercise_blocks(ramped())))
    halves = apply_block(block, spec(sets=4), Target(8, 30.0, lead=2))
    set_exercise_steps(payload, [halves], [])

    assert orders(payload) == [(1, 1), (2, 1), (3, 1), (4, 2), (5, 2), (6, 2)]
