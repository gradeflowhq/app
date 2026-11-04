"""A saved marking that stops marking says why on the questions it marked, which keep their marks."""

import pytest
from django.test import Client
from django.urls import reverse
from gradeflow_core.editing import dump_yaml

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs.models import Job
from gradeflow_app.marking.answers.column import out_of_date_notices
from gradeflow_app.marking.checks import assessment_checks
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.remarks import remark, schedule_marking
from gradeflow_app.results.models import Result
from tests.conftest import full_marks_marking

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("grouped", [False, True], ids=["question", "question-group"])
@pytest.mark.parametrize("what_breaks", ["question-type", "definition"])
def test_a_marking_that_stops_validating_schedules_nothing_and_says_so(
    assessment: Assessment, user: User, client: Client, grouped: bool, what_breaks: str
) -> None:
    question = assessment.questions.get(key="Q1")
    q1_check = {"question": "Q1", "uses": "gradeflow/key-point@1", "with": {"point": "light"}}
    definition = (
        {
            "uses": "gradeflow/conditional@1",
            "with": {
                "condition": [{"question": "Q2", "uses": "gradeflow/full-marks@1"}],
                "then": [q1_check, {"question": "Q3", "uses": "gradeflow/full-marks@1"}],
            },
        }
        if grouped
        else q1_check
    )
    marking = Marking.objects.create(
        assessment=assessment, question=None if grouped else question, definition=dump_yaml(definition)
    )
    first = schedule_marking(marking, user)
    assert first is not None
    first.refresh_from_db()
    assert first.status == Job.Status.COMPLETED
    previous = list(Result.objects.values("student_id", "question_id", "proportion", "revision"))
    if what_breaks == "question-type":
        question.definition = {"type": "number", "max_marks": 1}
        question.save()
    else:
        marking.definition = "uses: course-unavailable@1"
        marking.save()
    job_count = Job.objects.count()
    remark(assessment, ["Q1"], user)
    assert Job.objects.count() == job_count
    assert assessment_checks(assessment) == {}
    assert out_of_date_notices(assessment_checks(assessment), ["Q1"]) == [], "its own column says why"
    page_url = (
        reverse("question_group", args=[assessment.pk, marking.pk])
        if grouped
        else reverse("question", args=[assessment.pk, question.pk])
    )
    response = client.get(page_url)
    assert response.status_code == 200
    heading = (
        "This marking can't mark until it's fixed"
        if what_breaks == "question-type"
        else "This marking isn't compatible with the current GradeFlow"
    )
    assert heading in response.text
    if grouped:
        shown = client.get(reverse("question", args=[assessment.pk, assessment.questions.get(key="Q3").pk]))
        assert heading in shown.text, "on each question it marked"
    if grouped:
        assert "No questions yet" not in response.text
        assert 'title="photosynthesis uses light"' in response.text
        assert "Read only" in response.text
    assert list(Result.objects.values("student_id", "question_id", "proportion", "revision")) == previous


def test_a_failed_run_says_the_marks_are_from_before(assessment: Assessment, user: User) -> None:
    marking = full_marks_marking(assessment, "Q1")
    schedule_marking(marking, user)
    marking.refresh_from_db()
    job = marking.jobs.get(revision=marking.revision)
    Job.objects.filter(pk=job.pk).update(status=Job.Status.FAILED, error="Ran past its time limit.")
    assert out_of_date_notices(assessment_checks(assessment), ["Q1"]) == [
        "Marking failed: Ran past its time limit. The marks shown are from before."
    ]
