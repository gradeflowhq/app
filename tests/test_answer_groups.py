from collections.abc import Sequence
from typing import cast

import pytest
from django.core.cache import cache
from gradeflow_core import AnswerComparison, QuestionResult, compare_answers

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.core_version import CORE_VERSION
from gradeflow_app.marking.answers import answer_groups, comparisons
from gradeflow_app.marking.answers.answer_groups import GroupingOptions, StudentAnswer, group
from gradeflow_app.marking.answers.column import grouping_options


def entries(*answers: tuple[str, str]) -> list[StudentAnswer]:
    """Entries for (answer, how the question reads it) pairs."""
    return [
        StudentAnswer(
            1.0, student_pk=str(index), encrypted_student_id=f"k{index}", answer=answer, normalized=normalized
        )
        for index, (answer, normalized) in enumerate(answers)
    ]


def grouped_answers(
    answers: list[StudentAnswer], options: GroupingOptions, comparison: AnswerComparison | None = None
) -> list[list[str]]:
    return sorted(
        sorted(entry.answer for entry in grouped.student_answers) for grouped in group(answers, options, comparison)
    )


def fake_comparison(*indices: list[int]) -> AnswerComparison:
    """A comparison joining the distinct answers, in sorted order, at `indices`: whatever the core found alike."""

    class FakeComparison:
        def groups(self, threshold: float = 0.85) -> list[list[int]]:
            return list(indices)

    return cast(AnswerComparison, FakeComparison())


@pytest.fixture
def similar_modes_asked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """How the page asks the core to compare answers; the core finds only equal answers alike."""
    similar_modes: list[str] = []

    def compare(texts: Sequence[str], *, similarity: str) -> AnswerComparison:
        similar_modes.append(similarity)
        return compare_answers(texts)

    monkeypatch.setattr(answer_groups, "compare_answers", compare)
    return similar_modes


def test_answers_group_by_how_the_question_reads_them() -> None:
    answers = entries(("42", "42"), ("42.0", "42"), ("41", "41"))
    assert grouped_answers(answers, GroupingOptions()) == [["41"], ["42", "42.0"]]
    assert not any(grouped.joined_by_similarity for grouped in group(answers, GroupingOptions()))


def test_answers_are_written_as_the_question_reads_them(assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    question_entries = answer_groups.student_answers(question)
    assert {entry.answer for entry in question_entries} >= {"42", "42.0", ""}
    assert all(entry.normalized == question.core_question.normalize(entry.answer) for entry in question_entries)


def test_without_a_prepared_comparison_meaning_groups_only_equal_answers(similar_modes_asked: list[str]) -> None:
    answers = entries(("H2O", "H2O"), ("water", "water"), ("waters", "waters"))
    group(answers, GroupingOptions(similarity="spelling"))
    assert similar_modes_asked == ["spelling"]
    similar_modes_asked.clear()
    group(answers, GroupingOptions(similarity="meaning"))
    assert similar_modes_asked == ["normalized"], "meaning waits for its comparison job"
    similar_modes_asked.clear()
    group(answers, GroupingOptions(similarity="meaning"), fake_comparison([0], [1], [2]))
    assert similar_modes_asked == []


def test_answers_without_feedback_group_by_answer() -> None:
    answers = entries(("ice floats", "ice floats"), ("it is lighter", "it is lighter"))
    assert grouped_answers(answers, GroupingOptions(group_by="feedback")) == [["ice floats"], ["it is lighter"]]


def test_feedback_groups_its_answers_and_answers_without_it_stay_apart() -> None:
    answers = entries(("a", "a"), ("b", "b"), ("A", "A"), ("a", "a"))
    answers[0].preview_result = QuestionResult(proportion=1, max_marks=1, feedback="Correct.")
    answers[1].preview_result = QuestionResult(proportion=1, max_marks=1, feedback="Correct.")
    assert grouped_answers(answers, GroupingOptions(group_by="feedback")) == [["A"], ["a"], ["a", "b"]]


def test_similarity_groups_identify_answers_that_need_separate_marking() -> None:
    answers = entries(("ice is denser", "ice is denser"), ("ice is less dense", "ice is less dense"))
    assert group(answers, GroupingOptions(similarity="spelling"), fake_comparison([0, 1]))[0].joined_by_similarity
    same = entries(("B", "b"), ("b", "b"))
    assert not group(same, GroupingOptions(similarity="spelling"), fake_comparison([0]))[0].joined_by_similarity
    assert not group(same, GroupingOptions())[0].joined_by_similarity


def test_prepared_groups_attach_every_student_and_sort_by_count(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = entries(("water", "water"), ("H2O", "H2O"), ("Water.", "Water."), ("water", "water"))
    comparison = fake_comparison([0], [1, 2])

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Prepared comparisons must be reused when the threshold changes.")

    monkeypatch.setattr(answer_groups, "compare_answers", forbidden)
    for threshold in (0.5, 0.9, 1.0):
        groups = group(answers, GroupingOptions(similarity="meaning", threshold=threshold), comparison)
        assert [answer_group.student_count for answer_group in groups] == [3, 1]
        assert sorted(groups[0].student_pks) == ["0", "2", "3"]
        assert groups[0].joined_by_similarity


def test_comparison_cache_is_bound_to_ordered_inputs_and_build(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    cache.clear()
    texts = ["Water", "water."]
    comparison = compare_answers(texts, similarity="normalized")
    comparisons.store_comparison(comparison, core_version=CORE_VERSION)
    assert comparisons.cached_comparison(texts) == comparison
    assert comparisons.cached_comparison(list(reversed(texts))) is None
    assert comparisons.cached_comparison([*texts, "H2O"]) is None
    monkeypatch.setattr(comparisons, "CORE_VERSION", "another-build")
    assert comparisons.cached_comparison(texts) is None


@pytest.mark.parametrize("threshold", ["nan", "inf", "-inf", "invalid"])
def test_nonfinite_threshold_uses_the_selected_mode_default(threshold: str) -> None:
    assert grouping_options({"group": "meaning", "threshold": threshold}).threshold == 0.85
