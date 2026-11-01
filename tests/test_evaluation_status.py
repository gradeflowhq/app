"""A saved marking that stops marking says why on the questions it marked, which keep their marks."""

import pytest
from django.test import Client
from django.urls import reverse
from gradeflow_core.editing import dump_yaml

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs.models import Job
from gradeflow_app.marking.answers.status import notices
from gradeflow_app.marking.checks import checks
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.remarks import remark, schedule_marking
from gradeflow_app.results.models import Result

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("grouped", [False, True], ids=["question", "group"])
@pytest.mark.parametrize("invalid", ["question-type", "definition"])
def test_a_marking_that_stops_validating_schedules_nothing_and_says_so(
    assessment: Assessment, user: User, client: Client, grouped: bool, invalid: str
) -> None:
    question = assessment.questions.get(key="Q1")
    check = {"question": "Q1", "uses": "gradeflow/key-point@1", "with": {"point": "light"}}
    definition = (
        {
            "uses": "gradeflow/conditional@1",
            "with": {
                "condition": [{"question": "Q2", "uses": "gradeflow/full-marks@1"}],
                "then": [check, {"question": "Q3", "uses": "gradeflow/full-marks@1"}],
            },
        }
        if grouped
        else check
    )
    marking = Marking.objects.create(
        assessment=assessment, question=None if grouped else question, definition=dump_yaml(definition)
    )
    first = schedule_marking(marking, user)
    assert first is not None
    first.refresh_from_db()
    assert first.status == Job.Status.COMPLETED
    previous = list(Result.objects.values("student_id", "question_id", "proportion", "revision"))
    if invalid == "question-type":
        question.definition = {"type": "number", "max_marks": 1}
        question.save()
    else:
        marking.definition = "uses: course-unavailable@1"
        marking.save()
    jobs = Job.objects.count()
    remark(assessment, ["Q1"], user)
    assert Job.objects.count() == jobs
    assert checks(assessment) == {}
    assert notices(checks(assessment), ["Q1"]) == [], "its own column says why"
    page = (
        reverse("question_group", args=[assessment.pk, marking.pk])
        if grouped
        else reverse("question", args=[assessment.pk, question.pk])
    )
    response = client.get(page)
    assert response.status_code == 200
    assert "Marks nothing until fixed" in response.content.decode()
    if grouped:
        shown = client.get(reverse("question", args=[assessment.pk, assessment.questions.get(key="Q3").pk]))
        assert "Marks nothing until fixed" in shown.content.decode(), "on each question it marked"
    if grouped:
        assert "No questions yet" not in response.content.decode()
        assert 'title="photosynthesis uses light"' in response.content.decode()
        assert "Read only" in response.content.decode()
    assert list(Result.objects.values("student_id", "question_id", "proportion", "revision")) == previous


def test_a_failed_run_says_the_marks_are_from_before(assessment: Assessment, user: User) -> None:
    marking = Marking.objects.create(
        assessment=assessment,
        question=assessment.questions.get(key="Q1"),
        definition=dump_yaml({"question": "Q1", "uses": "gradeflow/full-marks@1"}),
    )
    schedule_marking(marking, user)
    marking.refresh_from_db()
    job = marking.jobs.get(revision=marking.revision)
    Job.objects.filter(pk=job.pk).update(status=Job.Status.FAILED, error="Took too long.")
    assert notices(checks(assessment), ["Q1"]) == ["Marking failed: Took too long. The marks shown are from before."]
