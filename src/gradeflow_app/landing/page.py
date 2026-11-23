"""The landing page: what GradeFlow does, its demos and its features."""

from functools import cache
from typing import Any

from django.template.loader import render_to_string
from django.utils.text import get_text_list

from gradeflow_app.landing.by_example import by_example_view
from gradeflow_app.landing.content import core_output
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


@cache
def demos() -> dict[str, str]:
    """Each demo's HTML, by name. Rendered once, without the request: the demos' forms are inert and need no CSRF
    token."""
    context = {
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


def landing_context() -> dict[str, Any]:
    return {
        "github_url": GITHUB_URL,
        "sections": SECTIONS,
        "features": FEATURES,
        "other_methods": get_text_list(core_output().other_methods, "and"),
        "demos": demos(),
    }
