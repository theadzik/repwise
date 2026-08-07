"""Which declared exercise a Garmin step or a logged set belongs to.

Garmin names the same exercise differently depending on the payload: it
auto-detects the movement while you lift, so what an activity logs need not
match what the workout programs, and either can be null. Matching is therefore
by normalised name, falling back to the category when exactly one exercise
claims it.

That rule is this tool's own rather than anything Garmin's schema dictates, so
it lives here and not in the adapter. The planner matches specs to workout
steps with it and the checker matches them to exercise blocks, which is why it
is written once.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


def normalise(name: str) -> str:
    """Reduce a name to letters and digits for loose matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


class ExerciseIndex[Item]:
    """Items found by exercise name, with the category as a fallback.

    A category identifies an item only while exactly one claims it: two
    exercises in the same workout sharing a category cannot say which of them
    a step belongs to. Callers that need to tell "no match" from "ambiguous"
    ask `claiming` for the candidates and report the difference themselves.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, Item] = {}
        self._by_category: dict[str, list[Item]] = {}

    def add(
        self,
        item: Item,
        *,
        name: str | None = None,
        aliases: Iterable[str | None] = (),
        category: str | None = None,
    ) -> None:
        """Index one item.

        `name` is the authoritative one and overwrites whatever held that key.
        An alias is a second name the same item answers to, and never displaces
        an authoritative one.
        """
        for alias in aliases:
            if alias:
                self._by_name.setdefault(normalise(alias), item)
        if name:
            self._by_name[normalise(name)] = item
        if category:
            self._by_category.setdefault(normalise(category), []).append(item)

    def by_name(self, *names: str | None) -> Item | None:
        """The item behind the first of these names that is indexed."""
        for name in names:
            if not name:
                continue
            found = self._by_name.get(normalise(name))
            if found is not None:
                return found
        return None

    def claiming(self, category: str | None) -> list[Item]:
        """Every item declaring this category, ambiguity included."""
        if not category:
            return []
        return list(self._by_category.get(normalise(category), []))

    def by_category(self, category: str | None) -> Item | None:
        """The item this category identifies, when exactly one claims it."""
        claimed = self.claiming(category)
        return claimed[0] if len(claimed) == 1 else None

    def find(self, name: str | None = None, category: str | None = None) -> Item | None:
        """The full lookup: name first, then an unambiguous category."""
        found = self.by_name(name)
        return found if found is not None else self.by_category(category)
