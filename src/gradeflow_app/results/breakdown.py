"""A breakdown laid out: runs of checks that give the same facts, such as code tests, as a table, and other checks as
a list."""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby
from typing import Literal, NamedTuple

from gradeflow_core import CheckResult, Detail


class EffectIcon(NamedTuple):
    icon: str
    tone: str
    label: str


EFFECT_ICONS: dict[str, EffectIcon] = {
    "earned": EffectIcon("check", "text-success", "Earned marks"),
    "partial": EffectIcon("check", "text-warning", "Earned part of the marks"),
    "lost": EffectIcon("x-mark", "text-error", "Lost marks"),
    "uncounted": EffectIcon("minus", "text-subtle", "Didn't count"),
    "unmarked": EffectIcon("exclamation-triangle", "text-warning", "Needs marking"),
}
"""The icon, tone and label of each effect but yes and no, which show as words."""


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    rows: tuple[tuple[CheckResult, tuple[Detail | None, ...]], ...]
    has_marks: bool


type Block = tuple[Literal["check"], CheckResult] | tuple[Literal["table"], Table]


def laid_out(children: Sequence[CheckResult]) -> list[Block]:
    blocks: list[Block] = []
    for title, run in groupby(children, key=_first_detail):
        checks = list(run)
        if title is None or len(checks) == 1:
            blocks.extend(("check", check) for check in checks)
        else:
            blocks.append(("table", _table(checks)))
    return blocks


def _first_detail(check: CheckResult) -> str | None:
    """What a check's row lines up by in a table: its first detail, when it has a few."""
    return check.details[0].title if len(check.details) > 1 and not check.children else None


def _table(checks: list[CheckResult]) -> Table:
    facts = [{detail.title: detail for detail in check.details} for check in checks]
    columns = tuple(dict.fromkeys(title for row in facts for title in row))
    return Table(
        columns=columns,
        rows=tuple(
            (check, tuple(row.get(column) for column in columns)) for check, row in zip(checks, facts, strict=True)
        ),
        has_marks=any(check.marks is not None for check in checks),
    )
