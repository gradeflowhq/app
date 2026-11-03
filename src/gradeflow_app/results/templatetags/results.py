from collections.abc import Sequence

from django import template
from gradeflow_core import CheckResult

from gradeflow_app.results import breakdown
from gradeflow_app.results.marks import Level, level_of
from gradeflow_app.ui.templatetags.ui import marks

register = template.Library()


@register.simple_tag
def level(low: float, high: float, max_marks: float) -> Level:
    """Full, part or no marks, for marks from `low` to `high`: `{% level low high max_marks as level %}`."""
    return level_of(low, high, max_marks)


@register.filter
def laid_out(children: Sequence[CheckResult]) -> list[breakdown.Block]:
    return breakdown.laid_out(children)


@register.filter
def effect_icon(effect: str) -> breakdown.EffectIcon:
    return breakdown.EFFECT_ICONS[effect]


@register.filter
def signed_marks(value: float) -> str:
    """Marks a check gave or took off: +1, -0.5 or 0."""
    shown = marks(abs(value))
    return "0" if shown == "0" else f"+{shown}" if value > 0 else f"-{shown}"


@register.filter
def multiline(text: str) -> bool:
    return "\n" in text
