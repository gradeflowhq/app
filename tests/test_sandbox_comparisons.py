import json
from typing import Any

import gradeflow_core
import pytest
from gradeflow_core import AnswerComparison, compare_answers
from gradeflow_core.editing import RowResult

from tests.conftest import sandbox_tasks


def test_worker_compares_the_complete_set_once_and_sends_parts_under_the_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs: list[list[str]] = []
    events: list[dict[str, Any]] = []
    texts = ["\U0001f331" * 1000, '\\"' * 1000]
    expected = compare_answers(texts, similarity="exact").model_copy(update={"similarity": "meaning"})

    def compare(texts: list[str], *, similarity: str) -> AnswerComparison:
        assert similarity == "meaning"
        inputs.append(texts)
        return expected

    monkeypatch.setattr(gradeflow_core, "compare_answers", compare)
    compare_task = sandbox_tasks(events.append)["compare"]
    compare_task.__globals__["MAX_BATCH_BYTES"] = 1024
    compare_task({"texts": texts})
    assert inputs == [texts]
    assert len(events) > 1
    assert all(len(json.dumps(event).encode()) <= 1024 for event in events)
    assert [event["index"] for event in events] == list(range(len(events)))
    assert all(event["event"] == "compared" and event["total"] == len(events) for event in events)
    assert AnswerComparison.model_validate_json("".join(event["text"] for event in events)) == expected


def test_worker_reports_a_result_for_each_test_answer() -> None:
    events: list[dict[str, Any]] = []
    rows = [{"answers": {"Q1": "light"}}, {"answers": {"Q1": "dark"}}]
    test_request = {
        "method": "gradeflow/accepted-answers@1",
        "methods": [],
        "questions": {"Q1": {"type": "text"}},
        "question": "Q1",
        "inputs": {"answers": ["light"]},
        "rows": rows,
    }
    sandbox_tasks(events.append)["test"](test_request)
    [event] = events
    assert event["event"] == "tested"
    assert [RowResult.model_validate(item).answers for item in event["results"]] == [row["answers"] for row in rows]
