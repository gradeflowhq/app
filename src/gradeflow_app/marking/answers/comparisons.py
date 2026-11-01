"""Answer comparisons for grouping by meaning: worked out by a sandbox job, then cached by their texts."""

import hashlib
import json
from collections.abc import Sequence

from django.core.cache import cache
from django.db import transaction
from gradeflow_core import AnswerComparison

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.core_build import CORE_BUILD
from gradeflow_app.jobs import queue
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.models import Job
from gradeflow_app.live.topics import topic
from gradeflow_app.questions.models import Question


def comparison_key(texts: Sequence[str], *, core_build: str) -> str:
    identity = json.dumps([core_build, list(texts)], ensure_ascii=False)
    return "comparison:" + hashlib.sha256(identity.encode()).hexdigest()


def cached_comparison(texts: Sequence[str]) -> AnswerComparison | None:
    payload = cache.get(comparison_key(texts, core_build=CORE_BUILD))
    return AnswerComparison.model_validate_json(payload) if payload is not None else None


def store_comparison(comparison: AnswerComparison, *, core_build: str) -> None:
    cache.set(comparison_key(comparison.texts, core_build=core_build), comparison.model_dump_json(), timeout=None)


@transaction.atomic
def start_grouping(assessment: Assessment, question: Question, texts: Sequence[str], user: User | None) -> Job:
    assessment.lock()
    data: dict[str, object] = {"texts": list(texts)}
    running = assessment.jobs.running().filter(kind=Job.Kind.GROUPING, questions=[question.key]).first()
    if running is not None:
        if running.input == data:
            return running
        queue.replace([running])
    return queue.enqueue(
        course=assessment.course,
        assessment=assessment,
        kind=Job.Kind.GROUPING,
        keys=[question.key],
        data=data,
        provenance={"comparison": comparison_key(texts, core_build=CORE_BUILD)},
        user=user,
    )


register(
    Job.Kind.GROUPING,
    Handler[AnswerComparison](
        task="compare",
        topics=lambda job: [topic("grouping", job.assessment_id)],
        finish=lambda job, comparison: store_comparison(comparison, core_build=job.core_build),
    ),
)
