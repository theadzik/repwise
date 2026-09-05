"""The table a plan prints as.

One row per exercise, in the order workouts.yaml puts them, with every column
saying one thing: what kind of change it is, where the exercise sits, what it
asks for, what the config would rewrite, and why.
"""

import logging

import pytest
from builders import spec

from repwise.app.report import report_plan
from repwise.app.report import rows as built_rows
from repwise.domain.models import Workout
from repwise.domain.progression import Target
from repwise.planner import (
    Change,
    GapChange,
    NameChange,
    NoteChange,
    Plan,
    RestChange,
    SetChange,
    StructureChange,
)

FIRST = spec(name="Barbell Back Squat", garmin_name="BARBELL_BACK_SQUAT")
MIDDLE = spec(name="Lat Pull-down", garmin_name="LAT_PULLDOWN_WIDE_GRIP")
LAST = spec(name="Face Pull", garmin_name="FACE_PULL")


@pytest.fixture(autouse=True)
def report_at_info(caplog):
    caplog.set_level(logging.INFO)


def a_plan(*, changes=(), warnings=(), **shaped):
    """A plan over three exercises, holding whatever a test hands it."""
    workout = Workout("Workout A", "1", ["trening a"], [FIRST, MIDDLE, LAST])
    return Plan(workout, {}, list(changes), list(warnings), **shaped)


def a_note(each):
    return NoteChange(each, "", "8-12 reps")


def rows(caplog):
    """The table without its heading, as whitespace-collapsed cells."""
    return [" ".join(record.message.split()) for record in caplog.records[1:]]


def heading(caplog):
    return " ".join(caplog.records[0].message.split())


def test_the_heading_names_every_column(caplog):
    report_plan(a_plan(notes=[a_note(FIRST)]))

    assert heading(caplog) == "# EXERCISE ACTION SETS BEFORE AFTER CONFIG WHY"


def test_an_exercise_is_one_row_wherever_workouts_yaml_puts_it(caplog):
    """Not in the order the planner decided things, and not once per kind of
    change it decided."""
    plan = a_plan(
        structure=[StructureChange("added", MIDDLE.name, 2, MIDDLE, Target(8, 30.0))],
        changes=[Change(LAST, Target(8, 30.0), Target(9, 30.0), "hit 8 on every set")],
        notes=[a_note(FIRST), a_note(LAST)],
    )

    report_plan(plan)

    assert [row.split()[2] for row in rows(caplog)] == ["Barbell", "Lat", "Face"]


def test_a_target_that_moved_takes_the_value_columns(caplog):
    plan = a_plan(
        changes=[Change(FIRST, Target(8, 30.0), Target(9, 30.0), "hit 8 on every set")],
        sets=[SetChange(FIRST, 3, 4)],
        notes=[a_note(FIRST)],
    )

    report_plan(plan)

    assert rows(caplog) == [
        "* 1 Barbell Back Squat advance 3 -> 4 8 x 30 kg -> 9 x 30 kg sets note "
        "hit 8 on every set"
    ]


def test_a_ramped_target_says_how_many_sets_are_ahead(caplog):
    """Rather than spelling out every set, which is what a set count is for."""
    plan = a_plan(
        changes=[
            Change(FIRST, Target(8, 30.0, lead=1), Target(8, 30.0, lead=2), "add 1 rep")
        ]
    )

    report_plan(plan)

    assert rows(caplog) == [
        "* 1 Barbell Back Squat advance 3 8+1 x 30 kg -> 8+2 x 30 kg add 1 rep"
    ]


def test_the_action_says_which_way_a_target_went(caplog):
    """A second miss in a row eases it, which is not an advance."""
    plan = a_plan(
        changes=[
            Change(FIRST, Target(9, 30.0), Target(8, 30.0), "missed twice, ease"),
            Change(MIDDLE, Target(9, 30.0), Target(9, 30.0), "repeat"),
        ]
    )

    report_plan(plan)

    assert "Barbell Back Squat ease" in rows(caplog)[0]
    assert "Lat Pull-down hold" in rows(caplog)[1]


def test_an_exercise_left_alone_has_a_blank_marker(caplog):
    """The one row that can be skipped: read, judged, nothing to write."""
    plan = a_plan(
        changes=[
            Change(FIRST, Target(9, 30.0), Target(9, 30.0), "repeat"),
            Change(MIDDLE, Target(9, 30.0), Target(9, 30.0), "repeat"),
        ],
        rests=[RestChange(MIDDLE, 90, 120)],
    )

    report_plan(plan)

    assert [record.message[0] for record in caplog.records[1:]] == [" ", "*"]


def test_a_rename_is_one_row_where_the_exercise_now_sits(caplog):
    """The removal it pairs with is folded into it rather than printed twice."""
    plan = a_plan(
        structure=[
            StructureChange(
                "added",
                MIDDLE.name,
                2,
                MIDDLE,
                Target(8, 30.0),
                replaces="LAT_PULLDOWN",
            ),
            StructureChange("removed", "LAT_PULLDOWN"),
        ],
        notes=[a_note(FIRST)],
    )

    report_plan(plan)

    assert rows(caplog) == [
        "* 1 Barbell Back Squat hold 3 note from workouts.yaml",
        "+ 2 Lat Pull-down build 3 -> 8 x 30 kg replaces LAT_PULLDOWN",
    ]


def test_a_move_says_where_it_came_from(caplog):
    """Where it is now is the column it is printed in."""
    plan = a_plan(structure=[StructureChange("moved", LAST.name, 3, previous=1)])

    report_plan(plan)

    assert rows(caplog) == ["~ 3 Face Pull move 3 from position 1"]


def test_a_genuine_removal_is_reported_after_the_exercises_that_remain(caplog):
    """It is not in workouts.yaml any more, so it has no place among them."""
    plan = a_plan(
        structure=[StructureChange("removed", "MYSTERY_LIFT")],
        notes=[a_note(FIRST), a_note(MIDDLE), a_note(LAST)],
    )

    report_plan(plan)

    assert rows(caplog)[-1] == "- MYSTERY_LIFT drop no longer in workouts.yaml"


def test_the_rest_between_exercises_is_the_last_row(caplog):
    """It belongs to the whole workout rather than to any one exercise."""
    plan = a_plan(notes=[a_note(FIRST)], gaps=GapChange(8, (None,) * 8, 30))

    report_plan(plan)

    assert rows(caplog)[-1] == (
        "* Between exercises retime lap button -> 30 s rest "
        "8 gap(s), from workouts.yaml"
    )


def test_warnings_come_last_whatever_they_are_about(caplog):
    """They are the only lines on stderr, so their place among the rest is
    not the report's to decide."""
    plan = a_plan(notes=[a_note(FIRST)], warnings=["Face Pull: not in the activity"])

    report_plan(plan)

    assert caplog.records[-1].message == "! Face Pull: not in the activity"


def test_the_workout_name_leads_the_table():
    """It renames the thing being listed, not something in it, so it goes
    above the exercises rather than after them."""
    plan = a_plan(
        name=NameChange("Gym Hinge", "Gym Deadlift"),
        gaps=GapChange(2, (None, None), 90),
        notes=[a_note(FIRST)],
    )
    built = built_rows(plan)

    assert built[0].name == "Workout name"
    assert built[0].action == "rename"
    assert (built[0].before, built[0].after) == ("Gym Hinge", "Gym Deadlift")
    assert built[-1].name == "Between exercises", "the gap still comes last"
