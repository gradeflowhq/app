"""Answer comparisons for grouping by meaning: worked out by a sandbox job, then cached by their texts."""

import hashlib
import json
from collections.abc import Sequence

from django.core.cache import cache
from django.db import transaction
from gradeflow_core import AnswerComparison

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.core_version import CORE_VERSION
from gradeflow_app.jobs import queue
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.models import Job
from gradeflow_app.live.topics import topic
from gradeflow_app.questions.models import Question


def comparison_key(texts: Sequence[str], *, core_version: str) -> str:
    cache_input = json.dumps([core_version, list(texts)], ensure_ascii=False)
    return "comparison:" + hashlib.sha256(cache_input.encode()).hexdigest()


def cached_comparison(texts: Sequence[str]) -> AnswerComparison | None:
    cached_json = cache.get(comparison_key(texts, core_version=CORE_VERSION))
    return AnswerComparison.model_validate_json(cached_json) if cached_json is not None else None


def store_comparison(comparison: AnswerComparison, *, core_version: str) -> None:
    cache.set(comparison_key(comparison.texts, core_version=core_version), comparison.model_dump_json(), timeout=None)


@transaction.atomic
def start_grouping(assessment: Assessment, question: Question, texts: Sequence[str], user: User) -> Job:
    assessment.lock()
    job_input: dict[str, object] = {"texts": list(texts)}
    running = assessment.jobs.running().filter(kind=Job.Kind.GROUPING, question_keys=[question.key]).first()
    if running is not None:
        if running.input == job_input:
            return running
        queue.cancel_jobs([running])
    return queue.enqueue(
        course=assessment.course,
        assessment=assessment,
        kind=Job.Kind.GROUPING,
        question_keys=[question.key],
        input=job_input,
        marked_with={"comparison": comparison_key(texts, core_version=CORE_VERSION)},
        user=user,
    )


register(
    Job.Kind.GROUPING,
    Handler(
        task="compare",
        topics=lambda job: [topic("grouping", job.assessment_id)],
        apply_outcome=lambda job, comparison: store_comparison(comparison, core_version=job.core_version),
    ),
)
