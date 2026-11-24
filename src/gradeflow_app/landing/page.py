"""The landing page: what GradeFlow does, its demos and its features."""

from functools import cache
from typing import Any

from django.http import HttpRequest
from django.template.loader import render_to_string
from django.utils.text import get_text_list
from gradeflow_core import BUILTINS
from gradeflow_core.editing import Position, compatible_methods

from gradeflow_app.landing.by_example import by_example_view
from gradeflow_app.landing.content import landing_content
from gradeflow_app.landing.demos import ACCESS
from gradeflow_app.landing.groups import groups_view
from gradeflow_app.landing.hero import hero_view
from gradeflow_app.landing.methods import method_views
from gradeflow_app.landing.trust import trust_view

GITHUB_URL = "https://github.com/gradeflowhq"
SECTIONS = {
    "trust": "Trust",
    "methods": "Methods",
    "groups": "Groups",
    "by-example": "By example",
    "features": "Features",
}
FEATURES = [
    (
        "arrow-up-tray",
        "Import from any platform",
        "ExamSoft and Examplify exams, Canvas, Moodle and Blackboard quizzes, CSV or Excel files, and zips of "
        "students' files.",
    ),
    (
        "paper-airplane",
        "Send marks to Canvas",
        "Students are matched by login, SIS ID, email or name, with feedback as comments. Or download the marks as a "
        "spreadsheet.",
    ),
    (
        "lock-closed",
        "Encrypted by default",
        "Student IDs, uploaded files and your Canvas and AI tokens are encrypted before they're stored. Only your "
        "course's members can read the IDs.",
    ),
    (
        "user-group",
        "Mark together",
        "Invite colleagues as owners, editors or viewers. Everyone sees marks change as they happen.",
    ),
    ("clock", "History", "Every marking keeps its history. Restore an earlier version at any time."),
    ("puzzle-piece", "Method library", "Save a marking as a method to use in other courses, or make it public."),
]
"""Each feature's icon, title and description."""


def demos() -> dict[str, str]:
    """Each demo's HTML, by name. Rendered with a blank request, not the visitor's: the demos' forms are inert and
    their live refreshes never stream."""
    context = {
        "request": HttpRequest(),
        "github_url": GITHUB_URL,
        "access": ACCESS,
        "hero": hero_view(),
        "trust": trust_view(),
        "methods": method_views(),
        "groups": groups_view(),
        "by_example": by_example_view(),
    }
    return {
        name: render_to_string(f"landing/{name}.html", context)
        for name in ("hero", "trust", "methods", "groups", "by_example")
    }


def other_methods() -> list[str]:
    """The labels of the methods for the page's questions that have no example, simplest first."""
    content = landing_content()
    question_sets = [content.hero.question_set, content.groups.question_set, content.by_example.question_set]
    question_sets += [example.question_set for example in content.examples.values()]
    compatible = {
        method.ref: method
        for question_set in question_sets
        for question in [None, *question_set]
        for method in compatible_methods(Position(question_set, question, is_in_group=question is None))
    }
    with_examples = {BUILTINS.find(example.method_ref).label for example in content.examples.values()}
    by_level = sorted(compatible.values(), key=lambda method: method.level != "basic")
    return list(dict.fromkeys(method.label for method in by_level if method.label not in with_examples))


@cache
def landing_context() -> dict[str, Any]:
    """The page's context, built on the first visit."""
    return {
        "github_url": GITHUB_URL,
        "sections": SECTIONS,
        "features": FEATURES,
        "other_methods": get_text_list(other_methods(), "and"),
        "demos": demos(),
    }
