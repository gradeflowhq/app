import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from functools import cache

from allauth.usersessions.models import UserSession
from django import template
from django.contrib.humanize.templatetags.humanize import naturalday
from django.template.defaultfilters import floatformat, pluralize
from django.utils import timezone
from django.utils.html import conditional_escape, format_html, json_script
from django.utils.safestring import SafeString, mark_safe
from django.utils.text import capfirst
from django.utils.timesince import timesince
from gradeflow_core import Question, Span
from gradeflow_core.editing import HINT_KEY
from heroicons.templatetags.heroicons import heroicon_mini, heroicon_outline
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
from mdit_py_plugins.dollarmath import dollarmath_plugin
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound
from ua_parser import parse

from gradeflow_app.live import topics
from gradeflow_app.ui.middleware import toasts

register = template.Library()


def _new_tab(renderer: RendererHTML, tokens: Sequence[Token], index: int, options: OptionsDict, env: EnvType) -> str:
    """A link that opens in a new tab, so a guide in a dialog keeps its place."""
    tokens[index].attrSet("target", "_blank")
    tokens[index].attrSet("rel", "noopener")
    return renderer.renderToken(tokens, index, options, env)


_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False})
# Formulas are left as LaTeX in `.math` elements for the browser to typeset. "$5 or $6" stays text.
_math_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True}).use(
    dollarmath_plugin, allow_labels=False, allow_space=False, allow_digits=False
)
for _renderer in (_markdown, _math_markdown):
    _renderer.add_render_rule("link_open", _new_tab)
_WORD_BREAKS = re.compile(r"[\s@.]+")
# Pygments' short token classes, such as code-k for keywords, which styles.css colours.
_CODE_FORMATTER = HtmlFormatter(nowrap=True, classprefix="code-")


@register.simple_tag
def icon(name: str, classes: str = "", *, mini: bool = False) -> SafeString:
    """A Heroicon, hidden from assistive technology: the element holding it carries the label. 16 px unless
    `classes` gives a size."""
    render_icon = heroicon_mini if mini else heroicon_outline
    size = "" if "size-" in classes else "size-4 "
    return mark_safe(render_icon(name, size=None, **{"class": f"shrink-0 {size}{classes}"}))


@register.simple_tag
def hx_vals(**values: object) -> str:
    """`hx-vals` JSON for any values, like names people typed:
    `hx-vals="{% hx_vals operation="open" target=path %}"`. Empty values are left out."""
    return json.dumps({name: value for name, value in values.items() if value not in (None, "") and value is not False})


@register.filter
def marks(value: float | None, places: int | None = None) -> str:
    """Marks as people write them: 2, 1.5, 0.33; or with a fixed number of decimals, `marks:2`, to line them up."""
    if value is None:
        return "—"
    if places is not None:
        return floatformat(value, places)
    return floatformat(value, 2).rstrip("0").rstrip(".")


@register.filter
def duration(seconds: float) -> str:
    """How long something took, as people say it: <1 s, 4 s, 1.6 min, 13 min."""
    if seconds < 1:
        return "<1 s"
    if seconds < 59.5:
        return f"{seconds:.0f} s"
    minutes = seconds / 60
    return f"{minutes:.1f}".removesuffix(".0") + " min" if minutes < 9.95 else f"{minutes:.0f} min"


@register.filter
def ago(moment: datetime) -> str:
    """How long ago, in its largest unit: "just now", "1 hour ago", "2 days ago"."""
    if timezone.now() - moment < timedelta(minutes=1):
        return "just now"
    return f"{timesince(moment, depth=1)} ago"


@register.filter
def history_day(moment: datetime) -> str:
    """A day in a history: "Today", "Yesterday", "19 October", and "19 October 2025" in another year."""
    local = timezone.localtime(moment)
    return capfirst(naturalday(local, "j F" if local.year == timezone.localdate().year else "j F Y")) or ""


@register.filter
def initials(name: object) -> str:
    """The first letters of a name's first two words: "IB" for Introductory Biology, "QE" for qa@example.edu."""
    return "".join(word[0] for word in _WORD_BREAKS.split(str(name)) if word)[:2].upper()


@register.filter
def short_value(value: object) -> str:
    """A setting's value in a few words; a group of settings by its first text."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None:
        return "None"
    if isinstance(value, list):
        return ", ".join(map(short_value, value)) or "None"
    if isinstance(value, dict):
        return next(
            (text for text in value.values() if isinstance(text, str) and text),
            f"{len(value)} setting{pluralize(len(value))}",
        )
    return str(value)


@register.filter
def percent(value: float | None, total: float = 1) -> str:
    if value is None or not total:
        return "—"
    return f"{floatformat(100 * value / total, 0)}%"


@register.filter
def capped_percent(count: int, total: int) -> str:
    """`count` as a whole percentage of `total`, at most 99% until `count` is `total`: 199 of 200 is 99%."""
    percent = round(100 * count / total)
    return f"{percent if count == total else min(percent, 99)}%"


@register.filter
def code_language(question: Question | Mapping[str, Question] | None, key: str | None = None) -> str | None:
    """What the question's answers are written in when they are code, which only lines up in a fixed-width font:
    "text" for code in no named language. Of several questions, those of question ``key``."""
    if isinstance(question, Mapping):
        assert key is not None
        question = question.get(key)
    if question is None:
        return None
    answer_hint = question.answer_schema().get(HINT_KEY, {})
    return answer_hint.get("language", "text") if answer_hint.get("kind") == "code" else None


@register.filter
def answer_values(answer: str, question: Question | None) -> list[tuple[str, str]] | None:
    """`answer` as (title, value) pairs, one per value or list item. None if `answer` doesn't split into several
    values."""
    if question is None or not answer:
        return None
    try:
        values = question.answer_values(answer)
    except ValueError:
        return None
    properties = question.answer_schema()["properties"]
    if len(properties) < 2 and not any(isinstance(value, list) for value in values.values()):
        return None
    items = {name: value if isinstance(value, list) else [value] for name, value in values.items()}
    return [
        (property_schema.get("title", name), "" if item is None else str(item))
        for name, property_schema in properties.items()
        for item in items[name]
    ]


@register.simple_tag
def highlight_spans(text: str, spans: Iterable[Span], value_number: int | None = None) -> SafeString:
    """`text`, escaped, with each of `spans` in it wrapped in `<mark>`: those in value `value_number` of an answer,
    from 1, or in the whole answer if that is None."""
    html: list[str] = []
    position = 0
    for span in spans:
        if span.value_number == value_number:
            html += [
                conditional_escape(text[position : span.start]),
                format_html("<mark>{}</mark>", text[span.start : span.end]),
            ]
            position = span.end
    return mark_safe("".join([*html, conditional_escape(text[position:])]))


@cache
def _lexer(language: str) -> Lexer | None:
    try:
        return get_lexer_by_name(language, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return None


@register.filter
def highlight(text: str, language: str | None) -> SafeString:
    """``text`` coloured as code in ``language``; plain when there is none or Pygments doesn't know it."""
    lexer = language and _lexer(language)
    if not lexer:
        return conditional_escape(text)
    return mark_safe(pygments_highlight(text, lexer, _CODE_FORMATTER))


_QUOTED = re.compile("“[^”]*”")


@register.filter
def quotes_kept_together(text: str) -> SafeString:
    """``text`` with each “quoted” value kept on one line while it fits."""
    return mark_safe(_QUOTED.sub(r'<span class="inline-block">\g<0></span>', conditional_escape(text)))


@register.filter
def markdown(text: str) -> SafeString:
    """Markdown from the core, with raw HTML left as text."""
    return mark_safe(str(_markdown.render(text)))


@register.filter
def inline_markdown(text: str) -> SafeString:
    """A line of markdown from the core, without a paragraph around it."""
    return mark_safe(str(_markdown.renderInline(text)))


@register.filter
def math_markdown(text: str) -> SafeString:
    """Markdown people write, such as a question's description or a method's guide, with LaTeX formulas between $
    signs."""
    return mark_safe(str(_math_markdown.render(text)))


@register.filter
def browser(user_agent: str) -> str:
    """A browser in words, such as "Chrome on Linux"."""
    parsed = parse(user_agent)
    if parsed.user_agent is None:
        return "Unknown browser"
    return f"{parsed.user_agent.family} on {parsed.os.family}" if parsed.os else parsed.user_agent.family


@register.filter
def current_first(sessions: Iterable[UserSession]) -> list[UserSession]:
    """This device, then the others, last seen first."""
    return sorted(sessions, key=lambda session: (not session.is_current(), -session.last_seen_at.timestamp()))


@register.simple_tag(takes_context=True)
def listen(context: template.Context, *about: object) -> str:
    """An `hx-trigger` value refreshing for `about`, like `{% listen "marks" question.pk %}`."""
    return topics.listen(context["request"], topics.topic(*about))


@register.simple_tag(takes_context=True)
def page_messages(context: template.Context) -> str:
    """The page's messages, for behaviours/messages.js to show."""
    toast_list = toasts(context.get("messages") or ())
    return json_script(toast_list, "messages") if toast_list else ""


@register.simple_tag(takes_context=True)
def live_stream(context: template.Context) -> str:
    """The element streaming the events the page listens for, once the page above it is rendered."""
    if (request := context.get("request")) is None or (url := topics.stream_url(request)) is None:
        return ""
    return format_html('<div id="live-stream" hidden hx-sse:connect="{}"></div>', url)
