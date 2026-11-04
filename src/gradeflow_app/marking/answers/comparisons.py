"""Answer comparisons for grouping by meaning: worked out by a sandbox run, then cached by their texts."""

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from django.core.cache import cache
from gradeflow_core import AnswerComparison

from gradeflow_app.core_version import CORE_VERSION
from gradeflow_app.jobs import runs
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.payloads import ComparisonRun
from gradeflow_app.jobs.runner import Run
from gradeflow_app.jobs.runs import RunState
from gradeflow_app.live.topics import topic
from gradeflow_app.questions.models import Question

GROUPING = "grouping"
FAILURE_LIFETIME = 24 * 60 * 60
"""Seconds a failed comparison says so, after which grouping by meaning tries again."""


def comparison_key(texts: Sequence[str], *, core_version: str) -> str:
    cache_input = json.dumps([core_version, list(texts)], ensure_ascii=False)
    return "comparison:" + hashlib.sha256(cache_input.encode()).hexdigest()


def cached_comparison(texts: Sequence[str]) -> AnswerComparison | None:
    cached_json = cache.get(comparison_key(texts, core_version=CORE_VERSION))
    return AnswerComparison.model_validate_json(cached_json) if cached_json is not None else None


def store_comparison(comparison: AnswerComparison, *, core_version: str) -> None:
    cache.set(comparison_key(comparison.texts, core_version=core_version), comparison.model_dump_json(), timeout=None)


def start_grouping(question: Question, texts: Sequence[str]) -> None:
    """Compare `texts`, the question's answers, unless that is already under way."""
    run = Run(GROUPING, str(question.pk), _key(texts))
    if any(active.key == run.key for active, _ in runs.active(GROUPING, question.pk)):
        return
    cache.set(_texts_key(run.key), list(texts), timeout=None)
    cache.delete(_failure_key(run.key))
    runs.start(run)


def grouping_state(question: Question, texts: Sequence[str]) -> RunState | None:
    """How comparing `texts`, the question's answers, is going, or why it failed."""
    key = _key(texts)
    if (
        state := next((state for run, state in runs.active(GROUPING, question.pk) if run.key == key), None)
    ) is not None:
        return state
    failure = cache.get(_failure_key(key))
    return RunState("failed", failure) if failure is not None else None


def _key(texts: Sequence[str]) -> str:
    return comparison_key(texts, core_version=CORE_VERSION).removeprefix("comparison:")[:16]


def _texts_key(key: str) -> str:
    return f"comparison-texts:{key}"


def _failure_key(key: str) -> str:
    return f"comparison-failure:{key}"


def _input(run: Run) -> ComparisonRun | None:
    texts = cache.get(_texts_key(run.key))
    return ComparisonRun(texts=texts) if texts is not None else None


def _store(run: Run, outcome: Any) -> None:
    comparison = AnswerComparison.model_validate(outcome)
    if list(comparison.texts) != cache.get(_texts_key(run.key)) or comparison.similarity != "meaning":
        raise ValueError("The answer comparison does not match the answers compared.")
    store_comparison(comparison, core_version=CORE_VERSION)
    cache.delete(_texts_key(run.key))


def _record_failure(run: Run, error: str) -> None:
    cache.set(_failure_key(run.key), error, timeout=FAILURE_LIFETIME)


def _topics(run: Run) -> list[str]:
    assessment_id = Question.objects.filter(pk=run.owner).values_list("assessment_id", flat=True).first()
    return [topic("grouping", assessment_id)] if assessment_id is not None else []


register(
    GROUPING,
    Handler(
        task="compare",
        topics=_topics,
        input=_input,
        is_current=lambda run: True,
        finish=_store,
        fail=_record_failure,
    ),
)
