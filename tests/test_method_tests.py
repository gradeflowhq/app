import pytest
from gradeflow_core import GradeFlowError, QuestionSet
from gradeflow_core.editing import RowResult

from gradeflow_app.libraries import method_tests
from gradeflow_app.libraries.method_tests import MethodTests

QUESTIONS = QuestionSet.model_validate({"Q1": {"type": "text", "max_marks": 2}, "Q2": {"type": "text", "max_marks": 3}})


@pytest.fixture
def tests() -> MethodTests:
    return method_tests.new_tests(QUESTIONS)


def result(answers: dict[str, str]) -> RowResult:
    return RowResult.model_validate(
        {
            "answers": answers,
            "matches_expected": None,
            "results": {
                "Q1": {"proportion": 1, "max_marks": 2, "feedback": "First feedback"},
                "Q2": {"proportion": 0.5, "max_marks": 3, "feedback": "Second feedback"},
            },
            "max_marks": 5,
        }
    )


def test_a_submissions_answers_stay_together(tests: MethodTests) -> None:
    method_tests.add_answer(tests, "Q1", "First")
    method_tests.add_answer(tests, "Q2", "Second")
    assert [row["answers"] for row in tests["rows"]] == [{"Q1": "First", "Q2": ""}, {"Q1": "", "Q2": "Second"}]
    method_tests.set_answer(tests, 0, "Q2", "Paired")
    method_tests.toggle_example(tests, 0)
    method_tests.set_answer(tests, 0, "Q1", "")
    assert tests["rows"][0] == {"answers": {"Q1": "", "Q2": "Paired"}, "is_example": True}
    assert [row.answers for row in method_tests.examples_of(tests)] == [{"Q1": "", "Q2": "Paired"}]
    method_tests.delete_row(tests, 0)
    assert tests["rows"] == [{"answers": {"Q1": "", "Q2": "Second"}, "is_example": False}]
    with pytest.raises(GradeFlowError, match="no longer there"):
        method_tests.set_answer(tests, 3, "Q1", "Gone")


def test_test_answers_are_limited(tests: MethodTests) -> None:
    for _ in range(method_tests.MAX_ROWS):
        method_tests.add_answer(tests, "Q1", "a")
    with pytest.raises(GradeFlowError, match="at most"):
        method_tests.add_answer(tests, "Q1", "a")


def test_questions_added_duplicated_and_removed_keep_rows_whole(tests: MethodTests) -> None:
    method_tests.add_answer(tests, "Q1", "a")
    copy = method_tests.duplicate_question(tests, "Q1")
    assert copy == "Q3"
    assert tests["rows"][0]["answers"] == {"Q1": "a", "Q2": "", "Q3": "a"}
    assert method_tests.questions_of(tests)[copy] == QUESTIONS["Q1"]
    method_tests.remove_question(tests, "Q2")
    assert tests["rows"][0]["answers"] == {"Q1": "a", "Q3": "a"}
    assert list(method_tests.questions_of(tests)) == ["Q1", "Q3"]


def test_results_show_one_questions_marks(tests: MethodTests) -> None:
    method_tests.add_answer(tests, "Q1", "a")
    method_tests.set_answer(tests, 0, "Q2", "b")
    [row] = method_tests.answer_rows(tests, "Q2", [result({"Q1": "a", "Q2": "b"})])
    assert (row.number, row.answer, row.marks) == (1, "b", 1.5)
    assert row.result is not None
    assert row.result.feedback == "Second feedback"
    [row] = method_tests.answer_rows(tests, "Q2", None)
    assert (row.answer, row.result, row.marks) == ("b", None, None)
