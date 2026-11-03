from collections.abc import Sequence

from django import template
from gradeflow_core import CheckResult

from gradeflow_app.results import breakdown
from gradeflow_app.results.marks import Level, level_of
from gradeflow_app.ui.templatetags.ui import marks

register = template.Library()


@register.simple_tag
def marks_level(lowest_marks: float, highest_marks: float, max_marks: float) -> Level:
    """Full, partial or no marks, for marks from `lowest_marks` to `highest_marks`:
    `{% marks_level lowest_marks highest_marks max_marks as marks_level %}`."""
    return level_of(lowest_marks, highest_marks, max_marks)


@register.filter
def breakdown_blocks(children: Sequence[CheckResult]) -> list[breakdown.Block]:
    return breakdown.breakdown_blocks(children)


@register.filter
def effect_icon(effect: str) -> breakdown.EffectIcon:
    return breakdown.EFFECT_ICONS[effect]


@register.filter
def signed_marks(value: float) -> str:
    """Marks a check gave, or took away as a penalty: +1, -0.5 or 0."""
    shown = marks(abs(value))
    return "0" if shown == "0" else f"+{shown}" if value > 0 else f"-{shown}"


@register.filter
def is_multiline(text: str) -> bool:
    return "\n" in text
