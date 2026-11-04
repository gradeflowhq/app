"""Runs of a method's test answers: a draft's or a version's."""

from typing import Any

from gradeflow_core import GradeFlowError, ModelEndpoint
from gradeflow_core.editing import RowResult
from pydantic import TypeAdapter

from gradeflow_app.jobs import runs
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.payloads import TestRun
from gradeflow_app.jobs.runner import Run
from gradeflow_app.jobs.runs import COMPLETED, RunState
from gradeflow_app.libraries.designer.workspace import DRAFT_TESTS, VERSION_TESTS, MethodWorkspace
from gradeflow_app.libraries.models import MethodDraftRecord, VersionTests
from gradeflow_app.live.topics import topic

_RESULTS_ADAPTER = TypeAdapter(list[RowResult])


def _kind(tests: MethodDraftRecord | VersionTests) -> str:
    return DRAFT_TESTS if isinstance(tests, MethodDraftRecord) else VERSION_TESTS


def run_tests(tests: MethodDraftRecord | VersionTests, model_endpoint: ModelEndpoint | None) -> None:
    """Mark the method's test answers as they are now, replacing the earlier run of `tests`."""
    runs.start(Run(_kind(tests), str(tests.pk), str(tests.revision)), model_endpoint)


def stop_tests(tests: MethodDraftRecord | VersionTests) -> None:
    runs.stop(_kind(tests), tests.pk)


def run_state(tests: MethodDraftRecord | VersionTests) -> tuple[RunState | None, bool]:
    """How the latest run of `tests` is going or went, and whether the tests changed since it started."""
    if (latest := next(iter(runs.active(_kind(tests), tests.pk)), None)) is not None:
        run, state = latest
        return state, run.key != str(tests.revision)
    if tests.run_revision is None:
        return None, False
    out_of_date = tests.run_revision != tests.revision
    return (RunState("failed", tests.failure) if tests.failure else COMPLETED), out_of_date


def tests_topic(tests: MethodDraftRecord | VersionTests) -> str:
    """What the page showing the runs of `tests` listens to, with their ID."""
    return "method-draft" if isinstance(tests, MethodDraftRecord) else "version-tests"


def _model(run: Run) -> type[MethodDraftRecord | VersionTests]:
    return MethodDraftRecord if run.kind == DRAFT_TESTS else VersionTests


def _tests(run: Run) -> MethodDraftRecord | VersionTests | None:
    return _model(run).objects.filter(pk=run.owner).first()


def _test_run(run: Run) -> TestRun | None:
    """What the run marks, while the tests are still the revision it was started for."""
    tests = _tests(run)
    if tests is None or str(tests.revision) != run.key:
        return None
    method = tests.method if isinstance(tests, MethodDraftRecord) else tests.version.method
    try:
        return MethodWorkspace(method, tests).test_run()
    except GradeFlowError:
        return None


def _finish(run: Run, outcome: Any) -> None:
    test_run = _test_run(run)
    results = _RESULTS_ADAPTER.validate_python(outcome)
    if test_run is None or [result.answers for result in results] != [row.answers for row in test_run.rows]:
        raise ValueError("The marking service returned results that don't match the test answers.")
    _model(run).objects.filter(pk=run.owner).update(
        run_revision=int(run.key), results=_RESULTS_ADAPTER.dump_python(results, mode="json"), failure=""
    )


def _fail(run: Run, error: str) -> None:
    _model(run).objects.filter(pk=run.owner).update(run_revision=int(run.key), results=None, failure=error)


def _topics(run: Run) -> list[str]:
    tests = _tests(run)
    return [topic(tests_topic(tests), tests.pk)] if tests is not None else []


def _lock(run: Run) -> None:
    _model(run).objects.select_for_update().filter(pk=run.owner).first()


_HANDLER = Handler(
    task="test",
    topics=_topics,
    input=_test_run,
    is_current=lambda run: _model(run).objects.filter(pk=run.owner, revision=run.key).exists(),
    finish=_finish,
    fail=_fail,
    lock=_lock,
)
register(DRAFT_TESTS, _HANDLER)
register(VERSION_TESTS, _HANDLER)
