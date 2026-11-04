"""The controls of schema forms beyond Django's own."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from django import forms
from gradeflow_core.editing import Condition, ConditionRow

from gradeflow_app.ui.forms import posted_indexes

DEFAULT_CONDITION_TEST = next(iter(Condition.test_labels))


class DecimalInput(forms.NumberInput):
    """Numbers as people write them: `2` and `0.000001`, not `2.0` and `1e-06`."""

    def format_value(self, value: Any) -> str | None:
        if isinstance(value, float):
            text = format(Decimal(repr(value)), "f")
            return text.rstrip("0").rstrip(".") if "." in text else text
        formatted: str | None = super().format_value(value)
        return formatted


class CodeInput(forms.Textarea):
    """A code editor (code.js). Redrawing the object it belongs to leaves the editor alone, so it keeps its size and
    the column its scroll position."""

    template_name = "forms/widgets/code.html"

    def __init__(self, language: str) -> None:
        super().__init__(attrs={"x-data": "codeEditor", "data-language": language})


class Segmented(forms.RadioSelect):
    template_name = "forms/widgets/segmented.html"


class OrderedChoices(forms.SelectMultiple):
    """Choices in order, where one may repeat: a numbered select for each (ordered.js)."""

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
    """Tom Select tags (tags.js) for any values, offering suggestions with their counts."""

    template_name = "forms/widgets/tags.html"

    def __init__(self, suggestions: Mapping[str, int] | None = None, create: bool = True) -> None:
        super().__init__(attrs={"x-data": "tagInput", "data-create": "true" if create else "false"})
        self.suggestions = dict(suggestions or {})

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        context = super().get_context(name, value, attrs)
        selected = context["widget"]["selected"] = context["widget"]["value"]
        suggestions = self.suggestions or dict.fromkeys((str(choice) for choice, _ in self.choices), 0)
        context["widget"]["suggestions"] = [
            (text, count)
            for text, count in sorted(suggestions.items(), key=lambda item: -item[1])
            if text not in selected
        ]
        return context

    def format_value(self, value: Any) -> list[str]:
        return [str(item) for item in value or []]


class ConditionWidget(forms.Widget):
    """A check's "Only when" condition, built from rows such as [Test ▾] [is correct ▾], all or any of them; any other
    condition is shown as text, as written."""

    template_name = "forms/widgets/condition.html"

    def __init__(self) -> None:
        super().__init__()
        self.checks: list[tuple[str, str]] = []
        """The earlier checks a condition may read, by path and label."""

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        context = super().get_context(name, value, attrs)
        condition = value if isinstance(value, Condition) else Condition() if not value else None
        context["widget"] |= {
            "as_text": condition is None,
            "rows": [*(condition.rows if condition else ()), ConditionRow("", DEFAULT_CONDITION_TEST)],
            "match": condition.match if condition else Condition().match,
            "checks": self.checks,
            "test_labels": Condition.test_labels,
            "match_labels": Condition.match_labels,
        }
        return context

    def value_from_datadict(self, data: Mapping[str, Any], files: Any, name: str) -> Condition | str | None:
        if f"{name}-text" in data:
            return str(data[f"{name}-text"])
        indexes = posted_indexes(data, f"{name}-check")
        if not indexes:
            return None
        rows = tuple(
            ConditionRow(str(data.get(f"{name}-check-{i}", "")), test)
            for i in indexes
            if (test := data.get(f"{name}-test-{i}", DEFAULT_CONDITION_TEST)) in Condition.test_labels
        )
        match = data.get(f"{name}-combine", "")
        return Condition(rows, match) if match in Condition.match_labels else Condition(rows)

    def value_omitted_from_data(self, data: Mapping[str, Any], files: Any, name: str) -> bool:
        return f"{name}-text" not in data and not posted_indexes(data, f"{name}-check")


class FeedbackWidget(forms.Widget):
    """A check's own feedback: a box for each outcome, as the core names them, or one template as text, as
    written."""

    template_name = "forms/widgets/feedback.html"

    def __init__(self, boxes: Sequence[tuple[str, str]], template_parts: Mapping[str, str]) -> None:
        super().__init__()
        self.boxes, self.template_parts = list(boxes), dict(template_parts)

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        context = super().get_context(name, value, attrs)
        feedback_by_outcome = value if isinstance(value, Mapping) else {}
        context["widget"] |= {
            "as_text": isinstance(value, str) and bool(value),
            "boxes": [((box, label), feedback_by_outcome.get(box, "")) for box, label in self.boxes],
            "template_parts": self.template_parts,
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
