from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby
from typing import Literal, NamedTuple

from django import template
from gradeflow_core import CheckResult, Detail

from gradeflow_app.results.marks import level_of
from gradeflow_app.ui.templatetags.ui import marks

register = template.Library()

register.simple_tag(level_of, name="marks_level")


class EffectIcon(NamedTuple):
    icon: str
    color_class: str
    label: str


EFFECT_ICONS: dict[str, EffectIcon] = {
    "earned": EffectIcon("check", "text-success", "Earned marks"),
    "partial": EffectIcon("check", "text-warning", "Earned part of the marks"),
    "no_marks": EffectIcon("x-mark", "text-error", "No marks"),
    "penalty": EffectIcon("minus-circle", "text-error", "Penalty"),
    "uncounted": EffectIcon("minus", "text-subtle", "Didn't count"),
    "unmarked": EffectIcon("exclamation-triangle", "text-warning", "Couldn't mark"),
}
"""The icon, color class and label of each effect but met and not met, which show as words."""


@register.filter
def effect_icon(effect: str) -> EffectIcon:
    return EFFECT_ICONS[effect]


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    rows: tuple[tuple[CheckResult, tuple[Detail | None, ...]], ...]
    has_marks: bool


type Block = tuple[Literal["check"], CheckResult] | tuple[Literal["table"], Table]


@register.filter
def breakdown_blocks(children: Sequence[CheckResult]) -> list[Block]:
    """A breakdown as blocks: runs of checks that give the same facts, such as code tests, as a table, and other
    checks as a list."""
    blocks: list[Block] = []
    for title, group in groupby(children, key=_table_key):
        checks = list(group)
        if title is None or len(checks) == 1:
            blocks.extend(("check", check) for check in checks)
        else:
            blocks.append(("table", _table(checks)))
    return blocks


def _table_key(check: CheckResult) -> str | None:
    """What a check's row lines up by in a table: its first detail, when it has a few."""
    return check.details[0].title if len(check.details) > 1 and not check.children else None


def _table(checks: list[CheckResult]) -> Table:
    details_by_title = [{detail.title: detail for detail in check.details} for check in checks]
    columns = tuple(dict.fromkeys(title for row in details_by_title for title in row))
    return Table(
        columns=columns,
        rows=tuple(
            (check, tuple(row.get(column) for column in columns))
            for check, row in zip(checks, details_by_title, strict=True)
        ),
        has_marks=any(check.marks is not None for check in checks),
    )


@register.filter
def signed_marks(value: float) -> str:
    """Marks a check gave, or took away as a penalty: +1, -0.5 or 0."""
    shown = marks(abs(value))
    return "0" if shown == "0" else f"+{shown}" if value > 0 else f"-{shown}"


@register.filter
def is_multiline(text: str) -> bool:
    return "\n" in text
