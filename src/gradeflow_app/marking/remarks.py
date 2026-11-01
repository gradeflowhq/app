"""Marking every student with the saved markings, as remark jobs whose results replace those of earlier runs."""

from collections.abc import Iterable, Sequence
from typing import Any

from django.db import transaction
from gradeflow_core import Check, MarkedSubmission, ModelEndpoint

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs import queue
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import Payload
from gradeflow_app.libraries.collection import Files, files
from gradeflow_app.live.topics import publish, topic
from gradeflow_app.marking.checks import check_for, questions_involved
from gradeflow_app.marking.models import Marking
from gradeflow_app.model_service.endpoints import asks_models, for_checks
from gradeflow_app.results.models import Result


@transaction.atomic
def remark(
    assessment: Assessment,
    keys: Iterable[str],
    user: User | None = None,
    model_endpoint: ModelEndpoint | None = None,
) -> None:
    """Mark again with each marking that reads or marks one of the changed questions."""
    assessment.lock()
    selected = set(keys)
    for marking in assessment.markings.select_related("question"):
        if questions_involved(marking, None) & selected:
            schedule_marking(marking, user, model_endpoint)


@transaction.atomic
def schedule_marking(
    marking: Marking, user: User | None = None, model_endpoint: ModelEndpoint | None = None
) -> Job | None:
    """Mark every student with the saved marking, replacing the results of earlier runs, asking language models at
    `model_endpoint`. A marking the core can't use marks nothing until it is fixed; its column says why."""
    marking.assessment.lock()
    current = (
        Marking.objects.select_for_update(of=("self",))
        .select_related("question", "assessment__course")
        .get(pk=marking.pk)
    )
    queue.replace(current.jobs.filter(status__in=Job.ACTIVE))
    current.revision += 1
    check = check_for(current)
    if check is not None:
        removed = set(current.output_keys) - check.questions_marked()
        Result.objects.filter(question__assessment=current.assessment, question__key__in=removed).delete()
        current.output_keys = sorted(check.questions_marked())
        current.input_keys = sorted(check.questions_read())
    Marking.objects.filter(pk=current.pk).update(
        revision=current.revision, output_keys=current.output_keys, input_keys=current.input_keys
    )
    marking.revision, marking.output_keys, marking.input_keys = (
        current.revision,
        current.output_keys,
        current.input_keys,
    )
    if check is None or not current.output_keys:
        return None
    endpoint = for_checks([check], model_endpoint)
    if endpoint is None and asks_models([check]):
        return None  # keeps the decisions it has; the column says it wasn't marked again
    return mark_students(
        current.assessment,
        check,
        files(current.uses.all()),
        endpoint,
        kind=Job.Kind.REMARK,
        user=user,
        marking=current,
        revision=current.revision,
    )


def mark_students(
    assessment: Assessment,
    check: Check,
    methods: Files,
    model_endpoint: ModelEndpoint | None,
    *,
    kind: Job.Kind,
    user: User | None,
    **job: Any,
) -> Job:
    """A job marking every student of the assessment with `check`, which uses `methods`."""
    payload = Payload(
        questions=assessment.question_set(),
        markings=[check.dump()],
        methods=list(methods),
        submissions=[student.submission for student in assessment.students.all()],
        model_endpoint=model_endpoint,
    )
    return queue.enqueue(
        course=assessment.course,
        assessment=assessment,
        kind=kind,
        keys=sorted(check.questions_marked()),
        data=payload.model_dump(mode="json"),
        provenance={"markings": payload.markings, "methods": payload.methods},
        user=user,
        **job,
    )


@transaction.atomic
def delete_marking(marking: Marking) -> None:
    """Delete a marking and its results."""
    marking.assessment.lock()
    current = Marking.objects.select_for_update(of=("self",)).select_related("question").get(pk=marking.pk)
    queue.replace(current.jobs.filter(status__in=Job.ACTIVE))
    Result.objects.filter(question__assessment=current.assessment, question__key__in=current.outputs).delete()
    current.delete()


def active_jobs(assessment: Assessment) -> list[Job]:
    return list(assessment.jobs.running().filter(kind=Job.Kind.REMARK).defer("input", "result", "provenance"))


def _lock(job: Job) -> None:
    if job.marking_id is not None:
        Marking.objects.select_for_update().filter(pk=job.marking_id).first()


def _current(job: Job) -> bool:
    if job.marking_id is None:
        return False
    marking = Marking.objects.filter(pk=job.marking_id).first()
    return (
        marking is not None
        and marking.revision == job.revision
        and sorted(marking.output_keys) == sorted(job.questions)
    )


def _store(job: Job, submissions: Sequence[MarkedSubmission]) -> None:
    assert job.assessment is not None
    assert job.revision is not None
    questions = {question.key: question for question in job.assessment.questions.filter(key__in=job.questions)}
    students = dict(job.assessment.students.values_list("key", "id"))
    Result.objects.filter(question__in=questions.values()).delete()
    Result.objects.bulk_create(
        Result(
            student_id=students[submission.student_id],
            question=question,
            proportion=result.proportion,
            feedback=result.feedback,
            method=result.method,
            marked=result.marked,
            language_models=list(result.models),
            marking_id=job.marking_id,
            revision=job.revision,
        )
        for submission in submissions
        if submission.student_id in students
        for key, question in questions.items()
        if (result := submission.results.get(key)) is not None
    )
    publish(topic("marks", job.assessment_id))


register(
    Job.Kind.REMARK,
    Handler[Sequence[MarkedSubmission]](
        task="mark",
        topics=lambda job: [topic("jobs", job.assessment_id)],
        lock=_lock,
        current=_current,
        finish=_store,
    ),
)
