"""Marking every student with the saved markings, as remark runs whose results replace those of earlier runs."""

from collections.abc import Iterable
from typing import Any

from django.db import transaction
from gradeflow_core import Marked, ModelEndpoint, Step, Submission
from pydantic import TypeAdapter

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs import runs
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.payloads import MarkingRun
from gradeflow_app.jobs.runner import Run
from gradeflow_app.jobs.runs import RunState
from gradeflow_app.libraries.collection import MethodFiles, method_files
from gradeflow_app.live.topics import notify_change, topic
from gradeflow_app.marking.checks import check_for, questions_involved
from gradeflow_app.marking.models import Marking
from gradeflow_app.results.models import Result

REMARK = "remark"
_SUBMISSIONS_ADAPTER = TypeAdapter(list[Marked[Submission]])
NO_MODEL_SERVICE = "no-model-service"
"""A marking's failure when the change came from someone without an AI service, so it wasn't marked again."""


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
def schedule_marking(marking: Marking, user: User, model_endpoint: ModelEndpoint | None = None) -> Run | None:
    """Mark every student with the saved marking, replacing the results of earlier runs, asking language models at
    `model_endpoint`. A marking the core can't use marks nothing until it is fixed; its column says why."""
    marking.assessment.lock()
    marking.refresh_from_db(
        from_queryset=Marking.objects.select_for_update(of=("self",)).select_related("question", "assessment__course")
    )
    runs.stop(REMARK, marking.pk)
    marking.revision += 1
    marking.failure = ""
    check = check_for(marking)
    if check is not None:
        removed = set(marking.questions_marked) - check.questions_marked()
        Result.objects.filter(question__assessment=marking.assessment, question__key__in=removed).delete()
        marking.questions_marked = sorted(check.questions_marked())
        marking.questions_read = sorted(check.questions_read())
    run = None
    if check is not None and marking.questions_marked:
        needs_endpoint = check.needs_model_endpoint()
        if needs_endpoint and model_endpoint is None:
            marking.failure = NO_MODEL_SERVICE  # keeps the results it has
        else:
            run = Run(REMARK, str(marking.pk), str(marking.revision))
            runs.start(run, model_endpoint if needs_endpoint else None)
    Marking.objects.filter(pk=marking.pk).update(
        revision=marking.revision,
        failure=marking.failure,
        questions_marked=marking.questions_marked,
        questions_read=marking.questions_read,
    )
    return run


def marking_run(assessment: Assessment, check: Step, methods: MethodFiles) -> MarkingRun:
    """Every student of the assessment marked with `check`, which uses `methods`, given only the answers it reads."""
    questions_read = check.questions_read()
    return MarkingRun(
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
    )


def marked_submissions(outcome: Any, assessment: Assessment, keys: Iterable[str]) -> list[Marked[Submission]]:
    """A mark run's outcome, which must give every student of the assessment a result for each of `keys`. Raises
    `ValueError` if it doesn't."""
    submissions = _SUBMISSIONS_ADAPTER.validate_python(outcome)
    expected = set(assessment.students.values_list("encrypted_id", flat=True))
    received = {submission.sheet.student_id for submission in submissions}
    missing_outputs = any(set(keys) - submission.results.keys() for submission in submissions)
    if received != expected or len(submissions) != len(received) or missing_outputs:
        raise ValueError("The marking service returned an incomplete set of student results.")
    return submissions


@transaction.atomic
def delete_marking(marking: Marking) -> None:
    """Delete a marking and its results."""
    marking.assessment.lock()
    current = Marking.objects.select_for_update(of=("self",)).select_related("question").get(pk=marking.pk)
    runs.stop(REMARK, current.pk)
    Result.objects.filter(
        question__assessment=current.assessment, question__key__in=current.questions_given_marks
    ).delete()
    current.delete()


def running_remarks(assessment: Assessment) -> list[tuple[Marking, RunState]]:
    """The assessment's markings marking again, each with how its run is going."""
    active = {(run.owner, run.key): state for run, state in runs.active(REMARK)}
    return [
        (marking, state)
        for marking in assessment.markings.filter(pk__in=[owner for owner, _ in active])
        if (state := active.get((str(marking.pk), str(marking.revision)))) is not None
    ]


def _marking(run: Run) -> Marking | None:
    return Marking.objects.select_related("assessment").filter(pk=run.owner).first()


def _input(run: Run) -> MarkingRun | None:
    marking = _marking(run)
    if marking is None or str(marking.revision) != run.key or (check := check_for(marking)) is None:
        return None
    return marking_run(marking.assessment, check, method_files(marking.used_versions.all()))


def _lock(run: Run) -> None:
    if (marking := _marking(run)) is not None:
        marking.assessment.lock()
        Marking.objects.select_for_update().filter(pk=marking.pk).first()


def _is_current(run: Run) -> bool:
    return Marking.objects.filter(pk=run.owner, revision=run.key).exists()


def _store_results(run: Run, outcome: Any) -> None:
    marking = _marking(run)
    assert marking is not None
    assessment = marking.assessment
    submissions = marked_submissions(outcome, assessment, marking.questions_marked)
    questions = {question.key: question for question in assessment.questions.filter(key__in=marking.questions_marked)}
    students = dict(assessment.students.values_list("encrypted_id", "id"))
    Result.objects.filter(question__in=questions.values()).delete()
    Result.objects.bulk_create(
        Result(
            student_id=students[submission.sheet.student_id],
            question=question,
            proportion=result.proportion,
            feedback=result.feedback,
            method_label=result.method_label,
            is_marked=result.is_marked,
            model_names=list(result.model_names),
            breakdown=result.breakdown and result.breakdown.model_dump(mode="json"),
            marking=marking,
            revision=marking.revision,
        )
        for submission in submissions
        for key, question in questions.items()
        if (result := submission.results.get(key)) is not None
    )
    notify_change(topic("marks", assessment.pk))


def _record_failure(run: Run, error: str) -> None:
    Marking.objects.filter(pk=run.owner).update(failure=error)


def _topics(run: Run) -> list[str]:
    marking = _marking(run)
    return [topic("jobs", marking.assessment_id)] if marking is not None else []


register(
    REMARK,
    Handler(
        task="mark",
        topics=_topics,
        input=_input,
        is_current=_is_current,
        finish=_store_results,
        fail=_record_failure,
        lock=_lock,
        priority=50,
    ),
)
