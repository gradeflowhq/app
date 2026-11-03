from django.test import Client, TestCase
from django.urls import reverse
from gradeflow_core import CheckResult, Detail, QuestionResult
from gradeflow_core.editing import dump_yaml

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.marking.answers.answer_groups import GroupBreakdown, commonest_breakdown
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.remarks import schedule_marking
from gradeflow_app.results.breakdown import Table, laid_out
from gradeflow_app.results.marks import Award
from gradeflow_app.results.models import Result

LIGHT_IN_FEW_WORDS = {
    "question": "Q1",
    "uses": "gradeflow/combination@1",
    "with": {
        "checks": [
            {"uses": "gradeflow/key-point@1", "with": {"point": "light"}},
            {"uses": "gradeflow/length@1", "with": {"max_length": 3, "count": "words"}},
        ]
    },
}


def code_test(expression: str, output: Detail, effect: str = "lost") -> CheckResult:
    facts = (Detail("Expression", expression, "code"), Detail("Expected", "1", "code"), output)
    return CheckResult(title=f"Makes `{expression}` give `1`.", effect=effect, details=facts)  # type: ignore[arg-type]


def test_a_marking_of_several_checks_shows_how_each_marked_an_answer(
    client: Client, assessment: Assessment, user: User
) -> None:
    question = assessment.questions.get(key="Q1")
    marking = Marking.objects.create(assessment=assessment, question=question, definition=dump_yaml(LIGHT_IN_FEW_WORDS))
    with TestCase.captureOnCommitCallbacks(execute=True):
        job = schedule_marking(marking, user)
    assert job is not None
    job.refresh_from_db()
    assert (job.status, job.error) == ("completed", "")
    assert Result.objects.filter(question=question).filter(breakdown__isnull=False).exists()
    page = client.get(reverse("question", args=[assessment.pk, question.pk])).content.decode()
    assert "All of these" in page
    assert "Has at most <code>3</code> words." in page
    assert "Lost marks" in page


def test_runs_of_checks_with_the_same_facts_line_up_in_a_table() -> None:
    error = Detail("Output", "IndexError: list index out of range", "error")
    passed, failed = code_test("f(1)", Detail("Output", "1", "code"), "earned"), code_test("f([])", error)
    point = CheckResult(title="Includes `x`.", effect="earned")
    [(kind, table), (other, check)] = laid_out([passed, failed, point])
    assert (kind, other, check) == ("table", "check", point)
    assert isinstance(table, Table)
    assert table.columns == ("Expression", "Expected", "Output")
    assert [(row[0], row[1][-1]) for row in table.rows] == [(passed, passed.details[-1]), (failed, error)]


def test_checks_with_one_detail_each_are_not_a_table() -> None:
    why = (Detail("Reason", "No code."),)
    reasons = [CheckResult(title=title, effect="unmarked", details=why) for title in ("Recursion", "Loop")]
    assert laid_out(reasons) == [("check", reason) for reason in reasons]


def test_a_check_alone_is_not_a_table() -> None:
    alone = code_test("f(1)", Detail("Output", "2", "code"))
    assert laid_out([alone]) == [("check", alone)]


def test_answers_alike_show_the_breakdown_most_of_their_students_have() -> None:
    def award(effect: str) -> Award:
        check = CheckResult(title="Any one of these", effect=effect, children=(CheckResult(title="x", effect=effect),))  # type: ignore[arg-type]
        return Award(1, projected=QuestionResult(proportion=0, max_marks=1, breakdown=check))

    lost = award("lost").breakdown
    assert lost is not None
    shown = commonest_breakdown([award("lost"), award("earned"), award("lost")])
    assert shown == GroupBreakdown(lost, 2, 3)
    assert shown.note == "For 2 of 3 students"
    assert commonest_breakdown([Award(1)]) is None
