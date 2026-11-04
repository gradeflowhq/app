import json
import logging
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from gradeflow_core import AnswerComparison, MarkedSubmission, ModelEndpoint, compare_answers
from gradeflow_core.editing import dump_yaml
from pydantic import SecretStr
from pytest_django import DjangoCaptureOnCommitCallbacks, Settings

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs import runs
from gradeflow_app.jobs.payloads import MarkingRun
from gradeflow_app.jobs.runner import Ending, Launch, Run, State, Task
from gradeflow_app.live.topics import topic
from gradeflow_app.marking import previews
from gradeflow_app.marking.answers.column import PreviewSummary
from gradeflow_app.marking.answers.comparisons import cached_comparison, grouping_state, start_grouping
from gradeflow_app.marking.checks import definition_data
from gradeflow_app.marking.models import Marking, MarkingDraftRecord
from gradeflow_app.marking.remarks import remark, running_remarks
from gradeflow_app.results.models import Result
from tests.conftest import (
    HTMX_HEADERS,
    FakeRunner,
    code_draft,
    marked_by_sandbox,
    post_callback,
    preview_draft,
    save_full_marks_markings,
    signature,
    start_marking,
)

pytestmark = pytest.mark.django_db
type Start = Callable[[str, Callable[[], object]], str]


@pytest.fixture
def fully_marked(assessment: Assessment) -> Assessment:
    """The assessment with every question's marking saved, so marking them again starts a run."""
    return save_full_marks_markings(assessment)


@pytest.fixture
def start(fake_runner: FakeRunner, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks) -> Start:
    """Do what starts a run of a kind, and give the ID of the run it started."""

    def start_run(kind: str, action: Callable[[], object]) -> str:
        with django_capture_on_commit_callbacks(execute=True):
            action()
        return fake_runner.run_ids(kind)[-1]

    return start_run


def start_remark(start: Start, assessment: Assessment, key: str, user: User) -> str:
    return start("remark", lambda: remark(assessment, [key], user))


def marked_outcome(run_id: str) -> list[dict[str, Any]]:
    """What the sandbox gives the run with `run_id`, worked out in the test's process."""
    payload = run_input(run_id)
    return [submission.model_dump(mode="json") for submission in marked_by_sandbox(MarkingRun.model_validate(payload))]


def run_input(run_id: str) -> Any:
    run = Run.parse(run_id)
    assert run is not None
    response = Client().get(
        reverse("job_input", args=[run_id]), headers={"X-GradeFlow-Signature": signature(runs.secret(run), b"input")}
    )
    return response.json() if response.status_code == 200 else response.status_code


def test_drafts_preview_on_request(client: Client, assessment: Assessment, fake_runner: FakeRunner) -> None:
    url, fields = code_draft(client, assessment)
    response = client.post(url, fields)
    assert not fake_runner.run_ids("preview"), "changes don't preview by themselves"
    assert b'id="preview-bar"' in response.content
    draft = MarkingDraftRecord.objects.get()
    preview_draft(client, draft)
    preview_draft(client, draft)
    assert len(fake_runner.run_ids("preview")) == 1, "previewing an unchanged draft again starts none"
    draft.refresh_from_db()
    assert previews.preview_state(draft) == runs.COMPLETED
    answers = {student.encrypted_id for student in assessment.students.all()}
    assert {item["student_id"] for item in draft.preview_results or []} == answers
    assert not fake_runner.held, "the runner forgets a run once its outcome is in"
    client.post(url, fields | {"code": fields["code"] + "\n# changed"})
    preview_draft(client, draft)
    assert len(fake_runner.run_ids("preview")) == 2


def test_a_preview_by_another_build_of_the_core_is_run_again(
    client: Client, assessment: Assessment, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields)
    draft = MarkingDraftRecord.objects.get()
    preview_draft(client, draft)
    MarkingDraftRecord.objects.update(preview_results=[{"old": "shape"}])
    monkeypatch.setattr(previews, "CORE_VERSION", "a later build")
    page = client.get(reverse("answers", args=[assessment.pk, draft.question_id]))
    assert page.status_code == 200, "its results aren't read"
    assert "Marking changed" in page.text
    preview_draft(client, draft)
    assert len(fake_runner.run_ids("preview")) == 2


@pytest.mark.parametrize("view_name", ["draft_save", "draft_discard"])
def test_saving_or_discarding_a_draft_stops_its_preview(
    client: Client,
    assessment: Assessment,
    idle_runner: FakeRunner,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
    view_name: str,
) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields)
    draft = MarkingDraftRecord.objects.get()
    preview_draft(client, draft)
    [preview] = idle_runner.run_ids("preview")
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse(view_name, args=[assessment.pk, draft.pk]), {"path": "", "operation": "save"})
    assert preview not in idle_runner.held


def test_pages_hear_of_the_runs_they_show(
    client: Client,
    assessment: Assessment,
    fully_marked: Assessment,
    user: User,
    idle_runner: FakeRunner,
    start: Start,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields)
    draft = MarkingDraftRecord.objects.get()
    preview_draft(client, draft)
    [preview] = idle_runner.run_ids("preview")
    published: list[str] = []
    monkeypatch.setattr(runs, "notify_change", lambda *names: published.extend(names))
    post_callback(preview, {"event": "marked", "count": 1, "total": 10})
    post_callback(preview, {"event": "finished", "outcome": marked_outcome(preview)})
    assert published == [topic("preview", assessment.pk, user.pk)] * 2, "a preview concerns only whoever asked"
    published.clear()
    remark_run = start_remark(start, assessment, "Q1", user)
    post_callback(remark_run, {"event": "failed", "error": "Lost"})
    assert published == [topic("jobs", assessment.pk)]


@pytest.mark.parametrize(
    ("kind", "task", "priority"),
    [("grouping", "compare", 70), ("remark", "mark", 50), ("preview", "mark", 70), ("draft-tests", "test", 70)],
)
def test_each_kind_of_run_starts_with_its_task_the_apps_url_and_its_secret(
    settings: Settings,
    idle_runner: FakeRunner,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
    kind: str,
    task: Task,
    priority: int,
) -> None:
    settings.GRADEFLOW_APP_URL_FOR_JOBS = "http://172.17.0.2:8001"
    settings.GRADEFLOW_JOB_TIME_LIMIT = 60
    run = Run(kind, str(uuid.uuid7()), "1")
    with django_capture_on_commit_callbacks(execute=True):
        runs.start(run)
    assert idle_runner.launches[run.id] == Launch(
        task=task,
        priority=priority,
        time_limit=60,
        job_url=f"http://172.17.0.2:8001/jobs/{run.id}/",
        secret=runs.secret(run),
    )


def test_a_run_asking_language_models_gets_the_endpoint_and_more_time(
    settings: Settings, idle_runner: FakeRunner, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    endpoint = ModelEndpoint(url="https://models.example.com/v1", token=SecretStr("device-token"))
    run = Run("preview", str(uuid.uuid7()), "1")
    with django_capture_on_commit_callbacks(execute=True):
        runs.start(run, endpoint)
    launch = idle_runner.launches[run.id]
    assert launch.time_limit == settings.GRADEFLOW_MODEL_JOB_TIME_LIMIT
    endpoint_json = json.loads(launch.environment()["GRADEFLOW_MODEL_ENDPOINT"])
    assert (endpoint_json["url"], endpoint_json["token"]) == ("https://models.example.com/v1", "device-token")


def test_a_run_reads_its_input_only_while_what_it_is_for_is_unchanged(
    assessment: Assessment, fully_marked: Assessment, user: User, idle_runner: FakeRunner, start: Start
) -> None:
    run_id = start_remark(start, assessment, "Q1", user)
    payload = run_input(run_id)
    assert {key for submission in payload["submissions"] for key in submission["answers"]} == {"Q1"}, (
        "only the answers its marking reads"
    )
    Marking.objects.filter(question__key="Q1").update(revision=99)
    assert run_input(run_id) == 410


def test_callbacks_are_signed_and_fit_their_run(
    assessment: Assessment, fully_marked: Assessment, user: User, idle_runner: FakeRunner, start: Start
) -> None:
    run_id = start_remark(start, assessment, "Q1", user)
    assert post_callback(run_id, {"event": "finished", "outcome": []}, secret="forged") == 403
    assert post_callback(run_id, {"event": "tried", "trial": {}}) == 400
    assert (
        Client().post(reverse("job_callback", args=["gf.unknown.1.1.1"]), "{}", "application/json").status_code == 404
    )


def test_a_remark_shows_its_progress_then_its_results(
    client: Client, assessment: Assessment, fully_marked: Assessment, user: User, idle_runner: FakeRunner, start: Start
) -> None:
    run_id = start_remark(start, assessment, "Q1", user)
    idle_runner.held[run_id] = State.RUNNING
    assert post_callback(run_id, {"event": "progress", "step": "Deciding criteria", "completed": 3, "total": 4}) == 204
    assert post_callback(run_id, {"event": "marked", "count": 5, "total": 10}) == 204
    [(marking, state)] = running_remarks(assessment)
    assert (marking.question_id, state) == (
        assessment.questions.get(key="Q1").pk,
        runs.RunState("running", progress=0.5, progress_text="Deciding criteria: 3 of 4"),
    )
    listed = client.get(reverse("question_list", args=[assessment.pk]), {"question": ""}).text
    assert 'value="0.50" max="1"' in listed
    answers_column = client.get(reverse("answers", args=[assessment.pk, marking.question_id])).text
    assert "Marking…" in answers_column
    assert "Deciding criteria: 3 of 4" in answers_column
    assert post_callback(run_id, {"event": "finished", "outcome": marked_outcome(run_id)}) == 204
    assert run_id not in idle_runner.held
    assert Result.objects.filter(question__key="Q1").count() == assessment.students.count()
    assert running_remarks(assessment) == []


@pytest.mark.parametrize(
    ("outcome", "failure"),
    [
        ([{"student_id": "S1"}], runs.UNREADABLE_REASON),
        (None, runs.UNREADABLE_REASON),
        ([], "The marking service returned an incomplete set of student results."),
    ],
)
def test_a_remark_whose_outcome_doesnt_fit_fails(
    assessment: Assessment,
    fully_marked: Assessment,
    user: User,
    idle_runner: FakeRunner,
    start: Start,
    outcome: Any,
    failure: str,
) -> None:
    run_id = start_remark(start, assessment, "Q1", user)
    assert post_callback(run_id, {"event": "finished", "outcome": outcome}) == 204
    assert Marking.objects.get(question__key="Q1").failure == failure
    assert not Result.objects.filter(question__key="Q1").exists()


def test_a_failed_remark_says_why_until_marked_again(
    client: Client, assessment: Assessment, fully_marked: Assessment, user: User, idle_runner: FakeRunner, start: Start
) -> None:
    run_id = start_remark(start, assessment, "Q1", user)
    post_callback(run_id, {"event": "failed", "error": "Out of time."})
    question = assessment.questions.get(key="Q1")
    column = client.get(reverse("answers", args=[assessment.pk, question.pk])).text
    assert "Marking failed: Out of time. The marks shown are from before." in column
    assert run_id not in idle_runner.held
    start_remark(start, assessment, "Q1", user)
    assert Marking.objects.get(question=question).failure == ""


def test_a_newer_remark_stops_the_one_it_replaces(
    assessment: Assessment, fully_marked: Assessment, user: User, idle_runner: FakeRunner, start: Start
) -> None:
    first = start_remark(start, assessment, "Q1", user)
    second = start_remark(start, assessment, "Q1", user)
    assert first in idle_runner.stopped
    assert list(idle_runner.held) == [second]
    post_callback(first, {"event": "finished", "outcome": []})
    assert Marking.objects.get(question__key="Q1").failure == "", "a replaced run's outcome no longer applies"


def test_a_run_started_while_the_runner_is_down_fails_saying_so(
    assessment: Assessment,
    fully_marked: Assessment,
    user: User,
    idle_runner: FakeRunner,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    idle_runner.is_down = True
    with django_capture_on_commit_callbacks(execute=True):
        remark(assessment, ["Q1"], user)
    assert Marking.objects.get(question__key="Q1").failure == runs.UNAVAILABLE_REASON
    assert running_remarks(assessment) == [], "pages show nothing running"


@pytest.mark.parametrize(
    ("ending", "failure"),
    [
        (Ending.TIMED_OUT, "Ran past its time limit."),
        (Ending.OUT_OF_MEMORY, "Ran out of memory."),
        (Ending.FAILED, "The marking service stopped before finishing."),
        (Ending.EXITED, ""),
    ],
)
def test_a_run_that_ended_without_calling_back_fails_with_the_runners_reason(
    assessment: Assessment,
    fully_marked: Assessment,
    user: User,
    idle_runner: FakeRunner,
    start: Start,
    ending: Ending,
    failure: str,
) -> None:
    run_id = start_remark(start, assessment, "Q1", user)
    idle_runner.end(run_id, ending)
    assert Marking.objects.get(question__key="Q1").failure == failure
    assert run_id not in idle_runner.held


def test_pages_hear_as_the_runner_tells_of_a_change(
    assessment: Assessment,
    fully_marked: Assessment,
    user: User,
    idle_runner: FakeRunner,
    start: Start,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = start_remark(start, assessment, "Q1", user)
    published: list[str] = []
    monkeypatch.setattr(runs, "notify_change", lambda *names: published.extend(names))
    run = Run.parse(run_id)
    assert run is not None
    runs.listener.changed(run)
    runs.listener.changed(Run("retired-kind", "1", "1"))
    assert published == [topic("jobs", assessment.pk)]


def comparison_for(texts: list[str]) -> AnswerComparison:
    return compare_answers(texts, similarity="exact").model_copy(update={"similarity": "meaning"})


@pytest.fixture
def locmem_cache(settings: Settings) -> None:
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def test_grouping_caches_a_comparison_once_its_run_finishes(
    assessment: Assessment, locmem_cache: None, fake_runner: FakeRunner, start: Start
) -> None:
    texts = ["dark", "light"]
    question = assessment.questions.get(key="Q1")
    run_id = start("grouping", lambda: start_grouping(question, texts))
    assert run_input(run_id) == {"texts": texts}
    assert grouping_state(question, texts) == runs.RunState("queued")
    assert cached_comparison(texts) is None
    comparison = comparison_for(texts)
    assert post_callback(run_id, {"event": "finished", "outcome": comparison.model_dump(mode="json")}) == 204
    assert cached_comparison(texts) == comparison
    assert grouping_state(question, texts) is None


@pytest.mark.parametrize("outcome", ["failed", "absent", "invalid", "wrong_texts", "wrong_mode"])
def test_unsuccessful_comparisons_never_enter_the_cache(
    assessment: Assessment, locmem_cache: None, start: Start, outcome: str
) -> None:
    texts = [f"uncached {outcome}"]
    question = assessment.questions.get(key="Q1")
    run_id = start("grouping", lambda: start_grouping(question, texts))
    comparison: Any = comparison_for(texts).model_dump(mode="json")
    if outcome == "wrong_texts":
        comparison = comparison_for(["different answer"]).model_dump(mode="json")
    elif outcome == "wrong_mode":
        comparison = compare_answers(texts, similarity="exact").model_dump(mode="json")
    elif outcome == "invalid":
        comparison = {"texts": "not a comparison"}
    elif outcome == "absent":
        comparison = None
    event = (
        {"event": "failed", "error": "Lost"} if outcome == "failed" else {"event": "finished", "outcome": comparison}
    )
    assert post_callback(run_id, event) == 204
    state = grouping_state(question, texts)
    assert state is not None
    assert state.status == "failed"
    assert cached_comparison(texts) is None


def test_changed_answers_replace_the_grouping_under_way(
    assessment: Assessment, idle_runner: FakeRunner, start: Start
) -> None:
    question = assessment.questions.get(key="Q1")
    first = start("grouping", lambda: start_grouping(question, ["old"]))
    assert start("grouping", lambda: start_grouping(question, ["old"])) == first, "the same answers start none"
    second = start("grouping", lambda: start_grouping(question, ["new"]))
    assert first in idle_runner.stopped
    assert run_input(second) == {"texts": ["new"]}


def test_grouping_by_meaning_starts_from_the_page_and_not_on_reading_it(
    client: Client,
    assessment: Assessment,
    fake_runner: FakeRunner,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    question = assessment.questions.get(key="Q1")
    answers_url = reverse("answers", args=[assessment.pk, question.pk])
    grouping_url = reverse("answer_grouping", args=[assessment.pk, question.pk])
    due = client.get(answers_url, {"group": "meaning"}, headers=HTMX_HEADERS).text
    assert not fake_runner.run_ids("grouping")
    assert f'hx-post="{grouping_url}" hx-trigger="load"' in due
    with django_capture_on_commit_callbacks(execute=True):
        client.post(grouping_url, headers=HTMX_HEADERS)
    grouping_page = client.get(answers_url, headers=HTMX_HEADERS).text
    assert "Grouping by meaning." in grouping_page
    assert 'hx-trigger="load"' not in grouping_page
    with django_capture_on_commit_callbacks(execute=True):
        client.post(grouping_url, headers=HTMX_HEADERS)
    [run_id] = fake_runner.run_ids("grouping")
    assert post_callback(run_id, {"event": "failed", "error": "Lost"}) == 204
    failed = client.get(answers_url, headers=HTMX_HEADERS).text
    assert "Couldn't group by meaning" in failed
    assert f'hx-post="{grouping_url}" hx-target="#answers"' in failed, "trying again"
    assert 'hx-trigger="load"' not in failed
    with django_capture_on_commit_callbacks(execute=True):
        client.post(grouping_url, headers=HTMX_HEADERS)
    assert len(fake_runner.held) == 1


def test_failed_grouping_of_older_answers_does_not_block_current_answers(
    client: Client,
    assessment: Assessment,
    fake_runner: FakeRunner,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    question = assessment.questions.get(key="Q1")
    answers_url = reverse("answers", args=[assessment.pk, question.pk])
    grouping_url = reverse("answer_grouping", args=[assessment.pk, question.pk])
    client.get(answers_url, {"group": "meaning"}, headers=HTMX_HEADERS)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(grouping_url, headers=HTMX_HEADERS)
    [run_id] = fake_runner.run_ids("grouping")
    assert post_callback(run_id, {"event": "failed", "error": "Lost"}) == 204
    student = assessment.students.first()
    assert student is not None
    student.answers = student.answers | {"Q1": "A newly imported answer"}
    student.save(update_fields=["answers"])
    due = client.get(answers_url, headers=HTMX_HEADERS).text
    assert "Couldn't group by meaning" not in due
    assert f'hx-post="{grouping_url}" hx-trigger="load"' in due
    with django_capture_on_commit_callbacks(execute=True):
        client.post(grouping_url, headers=HTMX_HEADERS)
    assert len(fake_runner.held) == 1


def test_marking_questions_together_previews_on_request(client: Client, assessment: Assessment) -> None:
    created = client.post(
        reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/custom-code-for-a-group@1"}
    )
    page = created.headers["Location"]
    draft = MarkingDraftRecord.objects.get()
    definition = definition_data(draft.definition)
    definition["with"]["questions"] = ["Q1", "Q3"]
    draft.definition = dump_yaml(definition)
    draft.save()
    column = client.get(page).content
    assert b'id="preview-bar"' in column
    assert b"Q1, Q3" in column, "the answers column covers both questions"
    preview_draft(client, draft)
    draft.refresh_from_db()
    assert {key for item in draft.preview_results or [] for key in item["results"]} == {"Q1", "Q3"}
    answers = client.get(reverse("question_group_draft_answers", args=[assessment.pk, draft.pk])).content
    assert b"results change" in answers


def test_a_preview_counts_the_students_whose_marks_change(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["42"]})
    preview_draft(client, draft)
    draft.refresh_from_db()
    students = {student.encrypted_id: student for student in assessment.students.all()}
    for index, submission in enumerate(draft.preview_results or []):
        result = MarkedSubmission.model_validate(submission).results["Q2"]
        proportion = 1 - result.proportion if index == 0 else result.proportion
        Result.objects.create(
            student=students[submission["student_id"]],
            question=question,
            proportion=proportion,
            feedback=result.feedback,
            method_label="Q",
            revision=0,
        )
    page = client.get(reverse("answers", args=[assessment.pk, question.pk])).text
    preview_bar = page[page.index("preview-bar") :][:1500]
    assert "1 changed" in preview_bar, preview_bar


def test_editing_back_to_the_previewed_marking_shows_its_preview_again(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    previewed_fields = {"path": "", "fields_posted": "1", "points": ["light"], "combine": "all"}
    client.post(url, previewed_fields)
    preview_draft(client, draft)
    assert "Marking changed" in client.post(url, previewed_fields | {"combine": "any"}).text
    page = client.post(url, previewed_fields).text
    preview_bar = page[page.index('id="preview-bar"') :][:1500]
    assert "Marking changed" not in preview_bar
    assert " full · " in preview_bar, preview_bar


def test_a_preview_the_runner_forgot_can_be_run_again(
    client: Client, assessment: Assessment, idle_runner: FakeRunner
) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields)
    draft = MarkingDraftRecord.objects.get()
    preview_draft(client, draft)
    answers_url = reverse("answers", args=[assessment.pk, draft.question_id])
    assert "Waiting to start…" in client.get(answers_url).text
    idle_runner.held.clear()
    assert "Marking changed" in client.get(answers_url).text


def test_a_preview_says_when_answers_could_not_be_marked(client: Client, assessment: Assessment) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields | {"code": "raise ValueError('no such thing as results')"})
    draft = MarkingDraftRecord.objects.get()
    preview_draft(client, draft)
    draft.refresh_from_db()
    question = assessment.questions.get(key="Q1")
    preview_results = [MarkedSubmission.model_validate(item) for item in draft.preview_results or []]
    unmarked_count = sum(not submission.results["Q1"].is_marked for submission in preview_results)
    assert unmarked_count
    page = client.get(reverse("answers", args=[assessment.pk, question.pk])).text
    preview_bar = page[page.index("preview-bar") :][:3000]
    assert f"Couldn't mark {unmarked_count} students" in preview_bar
    assert "no such thing as results" in preview_bar
    unmarked = [card for card in page.split('<details class="group/answer')[1:] if "Couldn't mark</span>" in card]
    assert unmarked, "groups the draft couldn't mark say so instead of showing marks"
    assert not any("line-through" in card[: card.index("</summary>")] for card in unmarked)


def test_failed_runs_and_refused_callbacks_are_logged(
    assessment: Assessment,
    fully_marked: Assessment,
    user: User,
    idle_runner: FakeRunner,
    start: Start,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run_id = start_remark(start, assessment, "Q1", user)
    with caplog.at_level(logging.WARNING, "gradeflow_app"):
        post_callback(run_id, {"event": "finished"}, secret="forged")
        post_callback(run_id, {"event": "unknown"})
        post_callback(run_id, {"event": "failed", "error": "Out of memory."})
    assert [record.getMessage() for record in caplog.records if record.name.startswith("gradeflow_app")] == [
        f"Refused a callback for {run_id}: its signature doesn't match",
        f"Refused a callback for {run_id}",
        f"{run_id} failed: Out of memory.",
    ]


@pytest.mark.parametrize(
    ("changed", "expected"), [(1, "1 student's results change"), (2, "2 students' results change")]
)
def test_a_preview_across_questions_counts_students_plainly(changed: int, expected: str) -> None:
    draft = SimpleNamespace(assessment_id=uuid.uuid7(), pk=uuid.uuid7())
    summary = PreviewSummary(changed_count=changed, unmarked_count=0, problem="")
    preview_bar = render_to_string(
        "results/answers.html#preview_bar", {"draft": draft, "preview_summary": summary, "is_question_group": True}
    )
    assert expected in preview_bar.replace("&#x27;", "'")
