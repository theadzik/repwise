"""Where each line of a plan lands on the page.

A plan is read the way the workout is performed - top to bottom - so what the
report says about an exercise has to appear where that exercise is, whichever
kind of change said it.
"""

import logging

import pytest
from builders import spec

from repwise.app.report import report_plan
from repwise.domain.models import Workout
from repwise.domain.progression import Target
from repwise.planner import Change, NoteChange, Plan, StructureChange

FIRST = spec(name="Barbell Back Squat", garmin_name="BARBELL_BACK_SQUAT")
MIDDLE = spec(name="Lat Pull-down", garmin_name="LAT_PULLDOWN_WIDE_GRIP")
LAST = spec(name="Face Pull", garmin_name="FACE_PULL")


@pytest.fixture(autouse=True)
def report_at_info(caplog):
    caplog.set_level(logging.INFO)


def a_plan(*, structure=(), changes=(), notes=(), warnings=()):
    workout = Workout("Workout A", "1", ["trening a"], [FIRST, MIDDLE, LAST])
    return Plan(
        workout,
        {},
        list(changes),
        list(warnings),
        notes=list(notes),
        structure=list(structure),
    )


def a_note(each):
    return NoteChange(each, "", "8-12 reps")


def lines(caplog):
    return [record.message for record in caplog.records]


def test_an_exercise_added_in_the_middle_is_reported_in_the_middle(caplog):
    """Not first, which is where the planner happens to decide it."""
    plan = a_plan(
        structure=[
            StructureChange("added", MIDDLE.name, 2, MIDDLE, Target(8, 30.0)),
        ],
        notes=[a_note(FIRST), a_note(LAST)],
    )

    report_plan(plan)

    assert [line.split()[1] for line in lines(caplog)] == ["Barbell", "Lat", "Face"]


def test_a_rename_is_one_line_where_the_exercise_now_sits(caplog):
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
        notes=[a_note(FIRST), a_note(LAST)],
    )

    report_plan(plan)

    shown = lines(caplog)
    assert len(shown) == 3
    assert "replaces LAT_PULLDOWN, new at position 2" in shown[1]
    assert not [line for line in shown if line.startswith("-")]


def test_a_genuine_removal_is_reported_after_the_exercises_that_remain(caplog):
    """It is not in workouts.yaml any more, so it has no place among them."""
    plan = a_plan(
        structure=[StructureChange("removed", "MYSTERY_LIFT")],
        notes=[a_note(FIRST), a_note(MIDDLE), a_note(LAST)],
    )

    report_plan(plan)

    assert lines(caplog)[-1].startswith("- MYSTERY_LIFT")


def test_one_exercise_says_what_it_is_before_what_it_asks_for(caplog):
    """Its own lines keep the order the whole report used to have."""
    plan = a_plan(
        structure=[StructureChange("moved", MIDDLE.name, 2)],
        changes=[Change(MIDDLE, Target(8, 30.0), Target(9, 30.0), "hit")],
        notes=[a_note(MIDDLE)],
    )

    report_plan(plan)

    assert [line[0] for line in lines(caplog)] == ["~", "*", "*"]


def test_warnings_come_last_whatever_they_are_about(caplog):
    """They are the only lines on stderr, so their place among the rest is
    not the report's to decide."""
    plan = a_plan(notes=[a_note(FIRST)], warnings=["Face Pull: not in the activity"])

    report_plan(plan)

    assert lines(caplog)[-1] == "  ! Face Pull: not in the activity"
