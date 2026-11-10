import html
import re

from django.test import Client, TestCase
from django.urls import reverse
from gradeflow_core import CheckResult, Detail, QuestionResult
from gradeflow_core.editing import dump_yaml

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.marking.answers.answer_groups import GroupBreakdown, commonest_breakdown
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.remarks import schedule_marking
from gradeflow_app.results.marks import Award
from gradeflow_app.results.models import Result
from gradeflow_app.results.templatetags.results import Table, breakdown_blocks
from gradeflow_app.ui.templatetags.ui import inline_markdown
from tests.conftest import make_code_question

LIGHT_IN_AT_MOST_THREE_WORDS = {
    "question": "Q1",
    "uses": "gradeflow/workflow@1",
    "with": {
        "steps": [
            {"uses": "gradeflow/key-point@1", "with": {"point": "light"}},
            {"uses": "gradeflow/length@1", "with": {"max_length": 3, "unit": "words"}},
        ]
    },
}


def code_test_result(expression: str, output: Detail, effect: str = "no_marks") -> CheckResult:
    details = (Detail("Expression", expression, "code"), Detail("Expected", "1", "code"), output)
    return CheckResult(title=f"Makes `{expression}` give `1`.", effect=effect, details=details)  # type: ignore[arg-type]


def test_a_marking_of_several_checks_shows_how_each_marked_an_answer(
    client: Client, assessment: Assessment, user: User
) -> None:
    question = assessment.questions.get(key="Q1")
    marking = Marking.objects.create(
        assessment=assessment, question=question, definition=dump_yaml(LIGHT_IN_AT_MOST_THREE_WORDS)
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        schedule_marking(marking, user)
    marking.refresh_from_db()
    assert marking.failure == ""
    stored = Result.objects.filter(question=question, breakdown__isnull=False).first()
    assert stored is not None
    breakdown = CheckResult.model_validate(stored.breakdown)
    page = client.get(reverse("question", args=[assessment.pk, question.pk])).text
    assert all(inline_markdown(check.title) in page for check in (breakdown, *breakdown.children))
    assert "No marks" in page


def test_a_breakdown_this_core_cant_read_is_left_out(client: Client, assessment: Assessment, user: User) -> None:
    question = assessment.questions.get(key="Q1")
    marking = Marking.objects.create(
        assessment=assessment, question=question, definition=dump_yaml(LIGHT_IN_AT_MOST_THREE_WORDS)
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        schedule_marking(marking, user)
    Result.objects.filter(question=question).update(
        breakdown={"title": "Saved by an earlier core", "effect": "renamed"}
    )
    page = client.get(reverse("question", args=[assessment.pk, question.pk]))
    assert page.status_code == 200
    assert "Saved by an earlier core" not in page.text


def test_the_breakdown_shows_the_fixed_code(client: Client, assessment: Assessment, user: User) -> None:
    question = make_code_question(assessment, "Q1")
    assessment.students.update(answers={"Q1": "def f():\nreturn 1"})
    tests = [{"expression": "f()", "expected": "1"}, {"expression": "f() + 1", "expected": "2"}]
    check = {
        "question": "Q1",
        "uses": "gradeflow/code-tests@1",
        "with": {"tests": tests, "fixes": {"indentation": True}},
    }
    marking = Marking.objects.create(assessment=assessment, question=question, definition=dump_yaml(check))
    with TestCase.captureOnCommitCallbacks(execute=True):
        schedule_marking(marking, user)
    marking.refresh_from_db()
    assert marking.failure == ""
    page = client.get(reverse("question", args=[assessment.pk, question.pk])).text
    text = html.unescape(re.sub(r"<span[^>]*>|</span>", "", page))
    assert re.search(r"Marked as\s*<code[^>]*>def f\(\):\n    return 1", text)


def test_runs_of_checks_with_the_same_details_line_up_in_a_table() -> None:
    error = Detail("Output", "IndexError: list index out of range", "error")
    passed, failed = code_test_result("f(1)", Detail("Output", "1", "code"), "earned"), code_test_result("f([])", error)
    key_point_result = CheckResult(title="Includes `x`.", effect="earned")
    [(kind, table), (second_kind, second_check)] = breakdown_blocks([passed, failed, key_point_result])
    assert (kind, second_kind, second_check) == ("table", "check", key_point_result)
    assert isinstance(table, Table)
    assert table.columns == ("Expression", "Expected", "Output")
    assert [(row[0], row[1][-1]) for row in table.rows] == [(passed, passed.details[-1]), (failed, error)]


def test_checks_with_one_detail_each_are_not_a_table() -> None:
    reason_details = (Detail("Reason", "No code."),)
    reasons = [CheckResult(title=title, effect="unmarked", details=reason_details) for title in ("Recursion", "Loop")]
    assert breakdown_blocks(reasons) == [("check", reason) for reason in reasons]


def test_a_check_alone_is_not_a_table() -> None:
    single_result = code_test_result("f(1)", Detail("Output", "2", "code"))
    assert breakdown_blocks([single_result]) == [("check", single_result)]


def test_answers_alike_show_the_breakdown_most_of_their_students_have() -> None:
    def award_with(effect: str) -> Award:
        check = CheckResult(title="Any one of these", effect=effect, children=(CheckResult(title="x", effect=effect),))  # type: ignore[arg-type]
        return Award(1, preview_result=QuestionResult(proportion=0, max_marks=1, breakdown=check))

    expected_breakdown = award_with("no_marks").breakdown
    assert expected_breakdown is not None
    commonest = commonest_breakdown([award_with("no_marks"), award_with("earned"), award_with("no_marks")])
    assert commonest == GroupBreakdown(expected_breakdown, 2, 3)
    assert commonest.note == "For 2 of 3 students"
    assert commonest_breakdown([Award(1)]) is None
