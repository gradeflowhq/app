from typing import Any

import gradeflow_core
import pytest
from gradeflow_core import AnswerComparison, compare_answers
from gradeflow_core.editing import RowResult

from tests.conftest import sandbox_tasks


def test_the_sandbox_compares_the_complete_set_once(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs: list[list[str]] = []
    texts = ["\U0001f331" * 1000, '\\"' * 1000]
    expected = compare_answers(texts, similarity="exact").model_copy(update={"similarity": "meaning"})

    def compare(texts: list[str], *, similarity: str) -> AnswerComparison:
        assert similarity == "meaning"
        inputs.append(texts)
        return expected

    monkeypatch.setattr(gradeflow_core, "compare_answers", compare)
    outcome = sandbox_tasks(lambda event: None)["compare"]({"texts": texts})
    assert inputs == [texts]
    assert AnswerComparison.model_validate(outcome) == expected


def test_the_sandbox_gives_a_result_for_each_test_answer() -> None:
    rows = [{"answers": {"Q1": "light"}}, {"answers": {"Q1": "dark"}}]
    test_request = {
        "method": "gradeflow/accepted-answers@1",
        "methods": [],
        "questions": {"Q1": {"type": "text"}},
        "question": "Q1",
        "settings": {"answers": ["light"]},
        "rows": rows,
    }
    outcome = sandbox_tasks(lambda event: None)["test"](test_request)
    assert [RowResult.model_validate(item).answers for item in outcome] == [row["answers"] for row in rows]


def test_the_sandbox_counts_students_marked_about_once_a_second_and_at_the_end() -> None:
    events: list[dict[str, Any]] = []
    run = {
        "questions": {"Q1": {"type": "text"}},
        "markings": [{"question": "Q1", "uses": "gradeflow/full-marks@1"}],
        "methods": [],
        "submissions": [{"student_id": f"S{index}", "answers": {"Q1": "light"}} for index in range(3)],
    }
    outcome = sandbox_tasks(events.append)["mark"](run)
    assert [item["student_id"] for item in outcome] == ["S0", "S1", "S2"]
    assert events == [{"event": "marked", "count": 1, "total": 3}, {"event": "marked", "count": 3, "total": 3}]
