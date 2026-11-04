"""Marking every student with the saved markings, as remark jobs whose results replace those of earlier runs."""

from collections.abc import Iterable, Sequence
from typing import Any

from django.db import transaction
from gradeflow_core import Check, MarkedSubmission, ModelEndpoint, Submission

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs import queue
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import MarkingRun
from gradeflow_app.libraries.collection import MethodFiles, method_files
from gradeflow_app.live.topics import notify_change, topic
from gradeflow_app.marking.checks import check_for, questions_involved
from gradeflow_app.marking.models import Marking
from gradeflow_app.results.models import Result


@transaction.atomic
def remark(
    assessment: Assessment,
    keys: Iterable[str],
    user: User,
    model_endpoint: ModelEndpoint | None = None,
) -> None:
    """Mark again with each marking that reads or marks one of the changed questions."""
    assessment.lock()
    changed_keys = set(keys)
    for marking in assessment.markings.select_related("question"):
        if questions_involved(marking, None) & changed_keys:
            schedule_marking(marking, user, model_endpoint)


@transaction.atomic
def schedule_marking(marking: Marking, user: User, model_endpoint: ModelEndpoint | None = None) -> Job | None:
    """Mark every student with the saved marking, replacing the results of earlier runs, asking language models at
    `model_endpoint`. A marking the core can't use marks nothing until it is fixed; its column says why."""
    marking.assessment.lock()
    marking.refresh_from_db(
        from_queryset=Marking.objects.select_for_update(of=("self",)).select_related("question", "assessment__course")
    )
    queue.cancel_jobs(marking.jobs.filter(status__in=Job.ACTIVE))
    marking.revision += 1
    check = check_for(marking)
    if check is not None:
        removed = set(marking.questions_marked) - check.questions_marked()
        Result.objects.filter(question__assessment=marking.assessment, question__key__in=removed).delete()
        marking.questions_marked = sorted(check.questions_marked())
        marking.questions_read = sorted(check.questions_read())
    Marking.objects.filter(pk=marking.pk).update(
        revision=marking.revision, questions_marked=marking.questions_marked, questions_read=marking.questions_read
    )
    if check is None or not marking.questions_marked:
        return None
    needs_endpoint = check.needs_model_endpoint()
    if needs_endpoint and model_endpoint is None:
        return None  # keeps the results it has; the column says it wasn't marked again
    return mark_students(
        marking.assessment,
        check,
        method_files(marking.used_versions.all()),
        model_endpoint if needs_endpoint else None,
        kind=Job.Kind.REMARK,
        user=user,
        marking=marking,
        revision=marking.revision,
    )


def mark_students(
    assessment: Assessment,
    check: Check,
    methods: MethodFiles,
    model_endpoint: ModelEndpoint | None,
    *,
    kind: Job.Kind,
    user: User,
    **job_fields: Any,
) -> Job:
    """A job marking every student of the assessment with `check`, which uses `methods`, given only the answers it
    reads."""
    questions_read = check.questions_read()
    payload = MarkingRun(
        questions=assessment.question_set(),
        markings=[check.dump()],
        methods=list(methods),
        submissions=[
            Submission(
                student_id=student.encrypted_id,
                answers={key: answer for key, answer in student.answers.items() if key in questions_read},
            )
            for student in assessment.students.all()
        ],
        model_endpoint=model_endpoint,
    )
    return queue.enqueue(
        course=assessment.course,
        assessment=assessment,
        kind=kind,
        question_keys=sorted(check.questions_marked()),
        input=payload.model_dump(mode="json"),
        marked_with={"markings": payload.markings, "methods": payload.methods},
        user=user,
        **job_fields,
    )


@transaction.atomic
def delete_marking(marking: Marking) -> None:
    """Delete a marking and its results."""
    marking.assessment.lock()
    current = Marking.objects.select_for_update(of=("self",)).select_related("question").get(pk=marking.pk)
    queue.cancel_jobs(current.jobs.filter(status__in=Job.ACTIVE))
    Result.objects.filter(
        question__assessment=current.assessment, question__key__in=current.questions_given_marks
    ).delete()
    current.delete()


def running_remarks(assessment: Assessment) -> list[Job]:
    return list(assessment.jobs.running().filter(kind=Job.Kind.REMARK).defer("input", "result", "marked_with"))


def _lock_marking(job: Job) -> None:
    if job.marking_id is not None:
        Marking.objects.select_for_update().filter(pk=job.marking_id).first()


def _is_current(job: Job) -> bool:
    if job.marking_id is None:
        return False
    marking = Marking.objects.filter(pk=job.marking_id).first()
    return (
        marking is not None
        and marking.revision == job.revision
        and sorted(marking.questions_marked) == sorted(job.question_keys)
    )


def _store_results(job: Job, submissions: Sequence[MarkedSubmission]) -> None:
    assert job.assessment is not None
    assert job.revision is not None
    questions = {question.key: question for question in job.assessment.questions.filter(key__in=job.question_keys)}
    students = dict(job.assessment.students.values_list("encrypted_id", "id"))
    Result.objects.filter(question__in=questions.values()).delete()
    Result.objects.bulk_create(
        Result(
            student_id=students[submission.student_id],
            question=question,
            proportion=result.proportion,
            feedback=result.feedback,
            method_label=result.method_label,
            is_marked=result.is_marked,
            model_names=list(result.model_names),
            breakdown=result.breakdown and result.breakdown.model_dump(mode="json"),
            marking_id=job.marking_id,
            revision=job.revision,
        )
        for submission in submissions
        if submission.student_id in students
        for key, question in questions.items()
        if (result := submission.results.get(key)) is not None
    )
    notify_change(topic("marks", job.assessment_id))


register(
    Job.Kind.REMARK,
    Handler(
        task="mark",
        topics=lambda job: [topic("jobs", job.assessment_id)],
        lock=_lock_marking,
        is_current=_is_current,
        apply_outcome=_store_results,
    ),
)
