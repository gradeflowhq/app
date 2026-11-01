from collections.abc import Sequence
from typing import cast

import pytest
from django.core.cache import cache
from gradeflow_core import AnswerComparison, QuestionResult, compare_answers

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.core_build import CORE_BUILD
from gradeflow_app.marking.answers import answer_groups, comparisons
from gradeflow_app.marking.answers.answer_groups import Entry, Options, group
from gradeflow_app.marking.answers.column import options


def entries(*answers: tuple[str, str]) -> list[Entry]:
    """Entries for (answer, how the question reads it) pairs."""
    return [
        Entry(1.0, student=str(n), key=f"k{n}", answer=answer, canonical=canonical)
        for n, (answer, canonical) in enumerate(answers)
    ]


def keys(answers: list[Entry], options: Options, comparison: AnswerComparison | None = None) -> list[list[str]]:
    return sorted(sorted(entry.answer for entry in grouped.entries) for grouped in group(answers, options, comparison))


def joined(*indices: list[int]) -> AnswerComparison:
    """A comparison joining the distinct answers, in sorted order, at `indices`: whatever the core found alike."""

    class Joined:
        def groups(self, threshold: float = 0.85) -> list[list[int]]:
            return list(indices)

    return cast(AnswerComparison, Joined())


@pytest.fixture
def modes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """How the page asks the core to compare answers; the core finds only equal answers alike."""
    asked: list[str] = []

    def compare(texts: Sequence[str], *, similar: str) -> AnswerComparison:
        asked.append(similar)
        return compare_answers(texts)

    monkeypatch.setattr(answer_groups, "compare_answers", compare)
    return asked


def test_answers_group_by_how_the_question_reads_them() -> None:
    answers = entries(("42", "42"), ("42.0", "42"), ("41", "41"))
    assert keys(answers, Options()) == [["41"], ["42", "42.0"]]
    assert not any(grouped.approximate for grouped in group(answers, Options()))


def test_answers_are_written_as_the_question_reads_them(assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    found = answer_groups.entries(question)
    assert {entry.answer for entry in found} >= {"42", "42.0", ""}
    assert all(entry.canonical == question.core.normalize(entry.answer) for entry in found)


def test_without_a_prepared_comparison_meaning_groups_only_equal_answers(modes: list[str]) -> None:
    answers = entries(("H2O", "H2O"), ("water", "water"), ("waters", "waters"))
    group(answers, Options(similar="spelling"))
    assert modes == ["spelling"]
    modes.clear()
    group(answers, Options(similar="meaning"))
    assert modes == ["normalized"], "meaning waits for its comparison job"
    modes.clear()
    group(answers, Options(similar="meaning"), joined([0], [1], [2]))
    assert modes == []


def test_answers_without_feedback_group_by_answer() -> None:
    answers = entries(("ice floats", "ice floats"), ("it is lighter", "it is lighter"))
    assert keys(answers, Options(by="feedback")) == [["ice floats"], ["it is lighter"]]


def test_feedback_groups_its_answers_and_answers_without_it_stay_apart() -> None:
    answers = entries(("a", "a"), ("b", "b"), ("A", "A"), ("a", "a"))
    answers[0].projected = QuestionResult(proportion=1, max_marks=1, feedback="Correct.")
    answers[1].projected = QuestionResult(proportion=1, max_marks=1, feedback="Correct.")
    assert keys(answers, Options(by="feedback")) == [["A"], ["a"], ["a", "b"]]


def test_similarity_groups_identify_answers_that_need_separate_marking() -> None:
    answers = entries(("ice is denser", "ice is denser"), ("ice is less dense", "ice is less dense"))
    assert group(answers, Options(similar="spelling"), joined([0, 1]))[0].approximate
    same = entries(("B", "b"), ("b", "b"))
    assert not group(same, Options(similar="spelling"), joined([0]))[0].approximate
    assert not group(same, Options())[0].approximate


def test_prepared_groups_attach_every_student_and_sort_by_count(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = entries(("water", "water"), ("H2O", "H2O"), ("Water.", "Water."), ("water", "water"))
    comparison = joined([0], [1, 2])

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Prepared comparisons must be reused when the threshold changes.")

    monkeypatch.setattr(answer_groups, "compare_answers", forbidden)
    for threshold in (0.5, 0.9, 1.0):
        result = group(answers, Options(similar="meaning", threshold=threshold), comparison)
        assert [g.count for g in result] == [3, 1]
        assert sorted(result[0].students) == ["0", "2", "3"]
        assert result[0].approximate


def test_comparison_cache_is_bound_to_ordered_inputs_and_build(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    cache.clear()
    texts = ["Water", "water."]
    comparison = compare_answers(texts, similar="normalized")
    comparisons.store_comparison(comparison, core_build=CORE_BUILD)
    assert comparisons.cached_comparison(texts) == comparison
    assert comparisons.cached_comparison(list(reversed(texts))) is None
    assert comparisons.cached_comparison([*texts, "H2O"]) is None
    monkeypatch.setattr(comparisons, "CORE_BUILD", "another-build")
    assert comparisons.cached_comparison(texts) is None


@pytest.mark.parametrize("threshold", ["nan", "inf", "-inf", "invalid"])
def test_nonfinite_threshold_uses_the_selected_mode_default(threshold: str) -> None:
    assert options({"group": "meaning", "threshold": threshold}).threshold == 0.85
