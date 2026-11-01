from django import template

from gradeflow_app.results.marks import Level, level_of

register = template.Library()


@register.simple_tag
def level(low: float, high: float, max_marks: float) -> Level:
    """Full, part or no marks, for marks from `low` to `high`: `{% level low high max_marks as level %}`."""
    return level_of(low, high, max_marks)
