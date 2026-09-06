#!/usr/bin/env python3
"""Coverage floors, global and per file.

Exits non-zero when the tree as a whole drops below `fail_under` in
`pyproject.toml`, or when any one module drops below `FILE_FLOOR` here.

The per-file half is the one that earns its place. `coverage report` has a
`--fail-under` and it is global only, which is exactly the number that hides
the problem: an audit of this tree found `app/listing.py` at 25% and
`app/importing.py` at 39% - two shipped commands with no test between them -
while the total sat at 94%, because twenty-eight well-tested modules averaged
them away. A global floor would not have caught that. A per-file floor catches
it the moment the module arrives.

Reads the data `coverage run` already wrote, so it costs nothing beyond the
suite that produced it.

**These are floors, not targets.** Coverage answers "was this line executed",
never "would a test notice a wrong answer": the same audit found three of its
four bugs in covered code, one of them in a module at 100%. Climbing towards
the number buys tests that execute without asserting, which is worse than the
gap they close. If a floor is in the way, the question is whether the module
should have tests, not whether the floor should move.
"""

import json
import os
import sys
import tempfile

import coverage

#: The lowest any single module may sit. Distant from where the suite actually
#: is - the weakest module is in the high eighties - because this is an alarm
#: for a module arriving untested, not a bar to clear. Raise it when the tree
#: has risen on its own, never to set a goal.
FILE_FLOOR = 80.0


def measured() -> tuple[float, float, list[tuple[str, float]]]:
    """The recorded run: the overall floor, the total, and every file.

    Read through `json_report`, which is the public way to ask coverage for its
    per-file numbers - `report` gives the total and prints the rest. The floor
    comes from coverage's own config, so `fail_under` in pyproject.toml is the
    one place the overall number is written.
    """
    cov = coverage.Coverage()
    cov.load()
    # `json_report` writes to a path rather than to a file object, unlike
    # `report`, so it gets a scratch one. Nothing is left behind.
    with tempfile.TemporaryDirectory() as scratch:
        written = os.path.join(scratch, "coverage.json")
        try:
            cov.json_report(outfile=written)
        except coverage.exceptions.NoDataError:
            # Run on its own, or after the data file was cleaned away. The hook
            # never sees this because pytest runs under `coverage run` first,
            # so the person seeing it is running the script by hand.
            raise SystemExit(
                "No coverage data to read. Run `.venv/bin/coverage run -m "
                "pytest -q` first, which is what the pytest hook does."
            ) from None
        with open(written) as fh:
            report = json.load(fh)

    scored = [
        (os.path.relpath(path), each["summary"]["percent_covered"])
        for path, each in report["files"].items()
    ]
    return (
        cov.config.fail_under,
        report["totals"]["percent_covered"],
        sorted(scored, key=lambda each: each[1]),
    )


def main() -> int:
    floor, total, scored = measured()

    print("# Coverage\n")
    print(f"{total:.1f}% overall, floor {floor:.0f}%")
    print(f"per module, floor {FILE_FLOOR:.0f}%\n")

    low = [(name, pc) for name, pc in scored if pc < FILE_FLOOR]
    for name, pc in scored[:5]:
        print(f"  {pc:5.1f}%  {name}")
    print()

    if low:
        print("## Below the per-module floor\n")
        for name, pc in low:
            print(f"- {name} is at {pc:.1f}%, under the {FILE_FLOOR:.0f}% floor")
        print()
    if total < floor:
        print(f"## Below the overall floor\n\n- {total:.1f}% against {floor:.0f}%\n")

    return 1 if low or total < floor else 0


if __name__ == "__main__":
    sys.exit(main())
