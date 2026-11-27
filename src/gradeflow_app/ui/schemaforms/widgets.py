"""The controls of schema forms beyond Django's own."""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from django import forms
from gradeflow_core.editing import CheckRow, Condition, ConditionRow, ConditionSubject, SettingRow

from gradeflow_app.ui.forms import DecimalNotationMixin, posted_indexes


class NumberTextInput(DecimalNotationMixin, forms.TextInput):
    """A number in a text box, so a fraction such as `1/3` can be typed."""


class CodeInput(forms.Textarea):
    """A code editor (components/code-editor.js). Redrawing the object it belongs to leaves the editor alone, so it
    keeps its size and the column its scroll position."""

    template_name = "forms/widgets/code.html"

    def __init__(self, language: str) -> None:
        super().__init__(attrs={"x-data": "codeEditor", "data-language": language})


class Segmented(forms.RadioSelect):
    template_name = "forms/widgets/segmented.html"


class OrderedChoices(forms.SelectMultiple):
    """Choices in order, where one may repeat: a numbered select for each (components/ordered-choices.js)."""

    template_name = "forms/widgets/ordered.html"

    def __init__(self, item: str) -> None:
        super().__init__()
        self.item = item

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        context = super().get_context(name, value, attrs)
        options = [(str(choice), str(label)) for choice, label in self.choices]
        context["widget"] |= {
            "item": self.item,
            "options": options,
            "rows": [str(choice) for choice in value or []] or [""],
        }
        return context


class TagsWidget(forms.SelectMultiple):
    """Tom Select tags (components/tag-input.js) for any values, offering suggestions with their counts."""

    template_name = "forms/widgets/tags.html"

    def __init__(self, suggestions: Mapping[str, int] | None = None, create: bool = True) -> None:
        super().__init__(attrs={"x-data": "tagInput", "data-create": "true" if create else "false"})
        self.suggestions = dict(suggestions or {})

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        context = super().get_context(name, value, attrs)
        selected = context["widget"]["value"]
        suggestions = self.suggestions or dict.fromkeys((str(choice) for choice, _ in self.choices), 0)
        context["widget"]["selected"] = [(text, suggestions.get(text, 0)) for text in selected]
        context["widget"]["suggestions"] = [
            (text, count)
            for text, count in sorted(suggestions.items(), key=lambda item: -item[1])
            if text not in selected
        ]
        return context

    def format_value(self, value: Any) -> list[str]:
        if value is None:
            return []
        return [str(item) for item in value] if isinstance(value, list) else [str(value)]


class ConditionWidget(forms.Widget):
    """A step's "Only when" condition, built from rows such as [Test ▾] [is correct ▾], all or any of them; any other
    condition is shown as text, as written."""

    template_name = "forms/widgets/condition.html"

    def __init__(self) -> None:
        super().__init__()
        self.subjects: list[ConditionSubject] = []
        """The earlier checks and settings a condition may test."""

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        context = super().get_context(name, value, attrs)
        condition = value if isinstance(value, Condition) else Condition() if not value else None
        subjects_by_row = {row: subject for subject in self.subjects for row in subject.rows}
        rows = [
            {
                "subject": _subject_key(subjects_by_row[row]),
                "test": _test_key(row),
                "tests": _tests(subjects_by_row[row]),
            }
            for row in (condition.rows if condition else ())
        ]
        context["widget"] |= {
            "as_text": condition is None,
            # The empty row's disabled tests keep the column's width.
            "rows": [*rows, {"subject": "", "test": "", "tests": _tests(self.subjects[0]) if self.subjects else []}],
            "match": condition.match if condition else Condition().match,
            "subjects": [(_subject_key(subject), subject.label) for subject in self.subjects],
            "offers_settings": any(isinstance(row, SettingRow) for row in subjects_by_row),
            "match_labels": Condition.match_labels,
        }
        return context

    def value_from_datadict(self, data: Mapping[str, Any], files: Any, name: str) -> Condition | str | None:
        if f"{name}-text" in data:
            return str(data[f"{name}-text"])
        indexes = posted_indexes(data, f"{name}-subject")
        if not indexes:
            return None
        subjects = {_subject_key(subject): subject for subject in self.subjects}
        rows = tuple(
            _posted_row(subject, str(data.get(f"{name}-test-{i}", "")))
            for i in indexes
            if (subject := subjects.get(str(data.get(f"{name}-subject-{i}", ""))))
        )
        match = data.get(f"{name}-combine", "")
        return Condition(rows, match) if match in Condition.match_labels else Condition(rows)

    def value_omitted_from_data(self, data: Mapping[str, Any], files: Any, name: str) -> bool:
        return f"{name}-text" not in data and not posted_indexes(data, f"{name}-subject")


def _subject_key(subject: ConditionSubject) -> str:
    """A check's path, or `settings.<name>` for a setting."""
    match next(iter(subject.rows)):
        case CheckRow(check=check):
            return check
        case SettingRow(setting=setting):
            return f"settings.{setting}"


def _test_key(row: ConditionRow) -> str:
    """A row's test as posted: a check's test, or a setting's value as JSON."""
    return row.test if isinstance(row, CheckRow) else json.dumps(row.value)


def _tests(subject: ConditionSubject) -> list[tuple[str, str]]:
    return [(_test_key(row), words) for row, words in subject.rows.items()]


def _posted_row(subject: ConditionSubject, test: str) -> ConditionRow:
    """The row of `subject` testing `test`, else its first."""
    return next((row for row in subject.rows if _test_key(row) == test), next(iter(subject.rows)))


class FeedbackWidget(forms.Widget):
    """A check's own feedback: a box for each outcome, as the core names them, or one template as text, as
    written."""

    template_name = "forms/widgets/feedback.html"

    def __init__(self, boxes: Sequence[tuple[str, str]]) -> None:
        super().__init__()
        self.boxes = list(boxes)

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        context = super().get_context(name, value, attrs)
        feedback_by_outcome = value if isinstance(value, Mapping) else {}
        context["widget"] |= {
            "as_text": isinstance(value, str) and bool(value),
            "boxes": [((box, label), feedback_by_outcome.get(box, "")) for box, label in self.boxes],
        }
        return context

    def value_from_datadict(self, data: Mapping[str, Any], files: Any, name: str) -> dict[str, str] | str | None:
        if f"{name}-text" in data:
            return str(data[f"{name}-text"])
        # As typed: a box saved while someone is still writing in it, as when they click Insert, keeps its spaces.
        feedback_by_outcome = {
            box: text for box, _ in self.boxes if (text := str(data.get(f"{name}-{box}", ""))).strip()
        }
        return feedback_by_outcome or None

    def value_omitted_from_data(self, data: Mapping[str, Any], files: Any, name: str) -> bool:
        return f"{name}-text" not in data and not any(f"{name}-{box}" in data for box, _ in self.boxes)
