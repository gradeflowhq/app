import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
from django.db import connection
from django.template.loader import render_to_string
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from gradeflow_core import AnswerComparison, MarkedSubmission, compare_answers
from gradeflow_core.editing import dump_yaml
from pytest_django import DjangoCaptureOnCommitCallbacks, Settings

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.core_version import CORE_VERSION
from gradeflow_app.jobs import events, queue
from gradeflow_app.jobs.executors import ExecutorUnavailable, Nomad
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import MarkingRun
from gradeflow_app.live.topics import topic
from gradeflow_app.marking.answers import comparisons
from gradeflow_app.marking.answers.column import PreviewSummary
from gradeflow_app.marking.answers.comparisons import cached_comparison, start_grouping
from gradeflow_app.marking.checks import definition_data
from gradeflow_app.marking.models import MarkingDraftRecord
from gradeflow_app.marking.remarks import remark, running_remarks
from gradeflow_app.results.models import Result
from tests.conftest import (
    HTMX_HEADERS,
    IdleExecutor,
    code_draft,
    marked_by_sandbox,
    post_callback,
    preview_draft,
    save_full_marks_markings,
    signature,
    start_marking,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def fully_marked(assessment: Assessment) -> Assessment:
    """The assessment with every question's marking saved, so marking them again runs a job."""
    return save_full_marks_markings(assessment)


def test_drafts_preview_on_request(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, fields = code_draft(client, assessment)
    response = client.post(url, fields)
    assert not Job.objects.filter(kind=Job.Kind.PREVIEW).exists(), "changes don't preview by themselves"
    assert b'id="preview-bar"' in response.content
    preview_url = reverse("draft_preview", args=[assessment.pk, MarkingDraftRecord.objects.get().pk])
    with django_capture_on_commit_callbacks(execute=True):
        client.post(preview_url)
    client.post(preview_url)
    assert Job.objects.filter(kind=Job.Kind.PREVIEW).count() == 1, "previewing an unchanged draft again adds none"
    job = Job.objects.get(kind=Job.Kind.PREVIEW)
    answers = {student.encrypted_id for student in assessment.students.all()}
    assert job.status == Job.Status.COMPLETED
    assert {item["student_id"] for item in job.result or []} == answers
    client.post(url, fields | {"code": fields["code"] + "\n# changed"})
    client.post(preview_url)
    assert Job.objects.filter(kind=Job.Kind.PREVIEW).count() == 2


def test_a_preview_another_build_of_the_core_ran_is_run_again(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields)
    preview_url = reverse("draft_preview", args=[assessment.pk, MarkingDraftRecord.objects.get().pk])
    with django_capture_on_commit_callbacks(execute=True):
        client.post(preview_url)
    Job.objects.filter(kind=Job.Kind.PREVIEW).update(core_version="an earlier build", result=[{"old": "shape"}])
    assert client.get(url).status_code == 200, "its results aren't read"
    client.post(preview_url)
    assert Job.objects.filter(kind=Job.Kind.PREVIEW).count() == 2


@pytest.mark.parametrize("view_name", ["draft_save", "draft_discard"])
def test_saving_or_discarding_a_draft_stops_its_preview(
    client: Client, assessment: Assessment, idle_executor: None, view_name: str
) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields)
    draft = MarkingDraftRecord.objects.get()
    client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    preview = Job.objects.get(kind=Job.Kind.PREVIEW)
    assert preview.is_active
    client.post(reverse(view_name, args=[assessment.pk, draft.pk]), {"path": "", "operation": "save"})
    preview.refresh_from_db()
    assert not preview.is_active


def test_pages_follow_jobs_as_they_run(
    client: Client,
    assessment: Assessment,
    fully_marked: Assessment,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    idle_executor: None,
) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields)
    published_topics: list[str] = []
    monkeypatch.setattr(events, "notify_change", lambda *names: published_topics.extend(names))
    client.post(reverse("draft_preview", args=[assessment.pk, MarkingDraftRecord.objects.get().pk]))
    job = Job.objects.get(kind=Job.Kind.PREVIEW)
    post_callback(job, {"event": "started"})
    post_callback(job, {"event": "finished"})
    assert published_topics == [topic("preview", assessment.pk, user.pk)] * 3, (
        "a preview concerns only whoever asked for it"
    )
    published_topics.clear()
    remark_job = start_remark(assessment, "Q1", user)
    post_callback(remark_job, {"event": "failed", "error": "Lost"})
    assert published_topics == [topic("jobs", assessment.pk)] * 2, "marks change only once a remark completes"


def test_a_user_may_run_only_so_many_jobs_at_once(
    assessment: Assessment, fully_marked: Assessment, user: User, settings: Settings
) -> None:
    settings.GRADEFLOW_RUNNING_JOBS_PER_USER = 1
    start_grouping(assessment, assessment.questions.get(key="Q1"), ["light"], user)
    queued = start_remark(assessment, "Q1", user)
    queued.refresh_from_db()
    assert queued.status == Job.Status.QUEUED
    assert queued.error == ""


def start_remark(assessment: Assessment, key: str, user: User) -> Job:
    """The job marking `key` again."""
    remark(assessment, [key], user)
    return assessment.jobs.filter(kind=Job.Kind.REMARK).latest("created_at")


def comparison_for(texts: list[str]) -> AnswerComparison:
    return compare_answers(texts, similarity="exact").model_copy(update={"similarity": "meaning"})


def compared_event(
    comparison: AnswerComparison, *, index: int = 0, total: int = 1, part: str | None = None
) -> dict[str, Any]:
    return {
        "event": "compared",
        "index": index,
        "total": total,
        "text": comparison.model_dump_json() if part is None else part,
    }


@pytest.fixture
def locmem_cache(settings: Settings) -> None:
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def test_grouping_caches_a_complete_comparison_only_after_success(
    assessment: Assessment, user: User, locmem_cache: None
) -> None:
    texts = ["dark", "light"]
    comparison = comparison_for(texts)
    job = start_grouping(assessment, assessment.questions.get(key="Q1"), texts, user)
    headers = {"X-GradeFlow-Signature": signature(job.secret, b"input")}
    input_response = Client().get(reverse("job_input", args=[job.pk]), headers=headers)
    assert input_response.json() == {"texts": texts}
    assert job.core_version == CORE_VERSION
    comparison_json = comparison.model_dump_json()
    first = compared_event(comparison, total=2, part=comparison_json[:10])
    assert post_callback(job, first) == 204
    assert post_callback(job, first) == 204, "retries do not append the same part twice"
    job.refresh_from_db()
    assert job.progress == 0.5
    assert cached_comparison(texts) is None
    assert post_callback(job, compared_event(comparison, index=1, total=2, part=comparison_json[10:])) == 204
    assert cached_comparison(texts) is None
    assert post_callback(job, {"event": "finished"}) == 204
    job.refresh_from_db()
    assert (job.status, job.input, job.result) == (Job.Status.COMPLETED, None, None)
    assert cached_comparison(texts) == comparison
    assert post_callback(job, first) == 204, "late callbacks leave a completed job alone"
    job.refresh_from_db()
    assert job.result is None


def test_a_comparison_is_cached_under_its_captured_build(
    assessment: Assessment, user: User, locmem_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue, "CORE_VERSION", "captured-grouping-build")
    texts = ["captured grouping answer"]
    comparison = comparison_for(texts)
    job = start_grouping(assessment, assessment.questions.get(key="Q1"), texts, user)
    assert post_callback(job, compared_event(comparison)) == 204
    assert post_callback(job, {"event": "finished"}) == 204
    assert cached_comparison(texts) is None
    monkeypatch.setattr(comparisons, "CORE_VERSION", job.core_version)
    assert cached_comparison(texts) == comparison


@pytest.mark.parametrize("outcome", ["failed", "absent", "missing", "invalid", "wrong_texts", "wrong_mode"])
def test_unsuccessful_comparisons_never_enter_the_cache(
    assessment: Assessment, user: User, locmem_cache: None, outcome: str
) -> None:
    texts = [f"uncached {outcome}"]
    comparison = comparison_for(texts)
    job = start_grouping(assessment, assessment.questions.get(key="Q1"), texts, user)
    if outcome == "wrong_texts":
        comparison = comparison_for(["different answer"])
    elif outcome == "wrong_mode":
        comparison = compare_answers(texts, similarity="exact")
    event = compared_event(comparison, total=2 if outcome == "missing" else 1)
    if outcome == "invalid":
        event["text"] = "not JSON"
    if outcome != "absent":
        assert post_callback(job, event) == 204
    assert post_callback(job, {"event": "failed" if outcome == "failed" else "finished", "error": "Lost"}) == 204
    job.refresh_from_db()
    assert job.status == Job.Status.FAILED
    assert job.result is None
    assert cached_comparison(texts) is None


def test_comparison_parts_must_arrive_in_order_and_duplicates_must_match(assessment: Assessment, user: User) -> None:
    texts = ["part validation"]
    comparison = comparison_for(texts)
    job = start_grouping(assessment, assessment.questions.get(key="Q1"), texts, user)
    assert post_callback(job, compared_event(comparison, index=1, total=2)) == 400
    assert post_callback(job, compared_event(comparison, total=0)) == 400
    assert post_callback(job, {"event": "compared"}) == 400
    assert post_callback(job, compared_event(comparison, total=2, part="first")) == 204
    assert post_callback(job, compared_event(comparison, total=2, part="different")) == 400
    assert post_callback(job, compared_event(comparison, index=1, total=3, part="next")) == 400


def test_changed_grouping_inputs_replace_the_pending_job(assessment: Assessment, user: User) -> None:
    question = assessment.questions.get(key="Q1")
    first = start_grouping(assessment, question, ["old"], user)
    assert start_grouping(assessment, question, ["old"], user) == first
    assert post_callback(first, compared_event(comparison_for(["old"]), total=2, part="{")) == 204
    second = start_grouping(assessment, question, ["new"], user)
    assert second != first
    first.refresh_from_db()
    assert first.status == Job.Status.FAILED
    assert post_callback(first, compared_event(comparison_for(["old"]))) == 204
    first.refresh_from_db()
    assert first.result is None
    assert second.input == {"texts": ["new"]}


def test_grouping_by_meaning_starts_from_the_page_and_not_on_reading_it(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    answers_url = reverse("answers", args=[assessment.pk, question.pk])
    start = reverse("answer_grouping", args=[assessment.pk, question.pk])
    due = client.get(answers_url, {"group": "meaning"}, headers=HTMX_HEADERS).text
    assert not Job.objects.filter(kind=Job.Kind.GROUPING).exists()
    assert f'hx-post="{start}" hx-trigger="load"' in due
    grouping_page = client.post(start, headers=HTMX_HEADERS).text
    assert "Grouping by meaning." in grouping_page
    assert 'hx-trigger="load"' not in grouping_page
    client.post(start, headers=HTMX_HEADERS)
    job = Job.objects.get(kind=Job.Kind.GROUPING)
    assert post_callback(job, {"event": "failed", "error": "Lost"}) == 204
    failed = client.get(answers_url, headers=HTMX_HEADERS).text
    assert "Couldn't group by meaning" in failed
    assert f'hx-post="{start}" hx-target="#answers"' in failed, "trying again"
    assert 'hx-trigger="load"' not in failed
    client.post(start, headers=HTMX_HEADERS)
    assert Job.objects.filter(kind=Job.Kind.GROUPING, status=Job.Status.QUEUED).count() == 1


def test_failed_grouping_of_older_answers_does_not_block_current_answers(
    client: Client, assessment: Assessment
) -> None:
    question = assessment.questions.get(key="Q1")
    answers_url = reverse("answers", args=[assessment.pk, question.pk])
    start = reverse("answer_grouping", args=[assessment.pk, question.pk])
    client.get(answers_url, {"group": "meaning"}, headers=HTMX_HEADERS)
    client.post(start, headers=HTMX_HEADERS)
    job = Job.objects.get(kind=Job.Kind.GROUPING)
    assert post_callback(job, {"event": "failed", "error": "Lost"}) == 204
    student = assessment.students.first()
    assert student is not None
    student.answers = student.answers | {"Q1": "A newly imported answer"}
    student.save(update_fields=["answers"])
    due = client.get(answers_url, headers=HTMX_HEADERS).text
    assert "Couldn't group by meaning" not in due
    assert f'hx-post="{start}" hx-trigger="load"' in due
    client.post(start, headers=HTMX_HEADERS)
    assert Job.objects.filter(kind=Job.Kind.GROUPING, status=Job.Status.QUEUED).count() == 1


def test_callbacks_are_signed_and_fit_their_job(assessment: Assessment, fully_marked: Assessment, user: User) -> None:
    job = start_grouping(assessment, assessment.questions.get(key="Q1"), ["light"], user)
    assert post_callback(job, {"event": "finished"}, secret="forged") == 403
    remark_job = start_remark(assessment, "Q1", user)
    assert post_callback(remark_job, {"event": "compared", "index": 0, "total": 1, "text": "{}"}) == 400


@pytest.mark.parametrize(
    ("kind", "task", "priority"),
    [
        (Job.Kind.GROUPING, "compare", 70),
        (Job.Kind.REMARK, "mark", 50),
        (Job.Kind.PREVIEW, "mark", 70),
        (Job.Kind.DRAFT_TESTS, "test", 70),
        (Job.Kind.VERSION_TESTS, "test", 70),
    ],
)
def test_nomad_runs_each_kind_of_job_in_the_image_it_recorded(
    settings: Settings, kind: Job.Kind, task: str, priority: int
) -> None:
    settings.GRADEFLOW_APP_URL_FOR_JOBS = "http://172.17.0.2:8001"
    nomad = Nomad(url="http://nomad", token="", datacenter="dc1", namespace="gradeflow")
    job = Job(pk=uuid.uuid7(), kind=kind, image="localhost:5055/sandbox@sha256:" + "a" * 64, time_limit=60)
    spec: Any = nomad.nomad_spec(job)
    sandbox_task = spec["TaskGroups"][0]["Tasks"][0]
    assert sandbox_task["Config"]["image"] == job.image
    assert [sandbox_task["Config"]["command"], *sandbox_task["Config"]["args"]] == [
        "python",
        "/app/sandbox.py",
        task,
        "60",
    ]
    assert sandbox_task["Env"]["GRADEFLOW_JOB_URL"] == f"http://172.17.0.2:8001/jobs/{job.pk}/"
    assert spec["Priority"] == priority
    assert spec["Namespace"] == "gradeflow"


def test_nomad_tells_which_of_the_jobs_it_received_have_ended() -> None:
    ended, running = uuid.uuid7(), uuid.uuid7()
    listed = [
        {"ID": f"gradeflow-{ended}", "Status": "dead"},
        {"ID": f"gradeflow-{running}", "Status": "running"},
        {"ID": "gradeflow-registry", "Status": "running"},
    ]
    nomad = Nomad(url="http://nomad", token="", datacenter="dc1", namespace="gradeflow")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.params["prefix"] == "gradeflow-"
        assert request.url.params["namespace"] == "gradeflow"
        return httpx.Response(200, json=listed)

    nomad.client = httpx.Client(base_url="http://nomad", transport=httpx.MockTransport(respond))
    assert nomad.submitted_jobs() == {ended: True, running: False}
    nomad.client = httpx.Client(base_url="http://nomad", transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    with pytest.raises(ExecutorUnavailable):
        nomad.submitted_jobs()
    with pytest.raises(ExecutorUnavailable):
        nomad.submit(Job(pk=ended, kind=Job.Kind.REMARK))


def test_results_arrive_in_batches_which_show_progress(
    client: Client, assessment: Assessment, fully_marked: Assessment, user: User, idle_executor: None
) -> None:
    job = start_remark(assessment, "Q1", user)
    job.refresh_from_db()
    submissions = [
        submission.model_dump(mode="json") for submission in marked_by_sandbox(MarkingRun.model_validate(job.input))
    ]
    half = len(submissions) // 2
    assert post_callback(job, {"event": "started"}) == 204
    assert post_callback(job, {"event": "marked", "submissions": submissions[:half]}) == 204
    job.refresh_from_db()
    assert job.progress == half / len(submissions)
    listed = client.get(reverse("question_list", args=[assessment.pk]), {"question": ""}).text
    assert f'value="{job.progress:.2f}" max="1"' in listed
    question = assessment.questions.get(key="Q1")
    answers_column = client.get(reverse("answers", args=[assessment.pk, question.pk])).text
    assert "Marking…" in answers_column
    assert f'value="{job.progress:.2f}" max="1"' in answers_column
    assert post_callback(job, {"event": "marked", "submissions": submissions[half:]}) == 204
    assert job.batches.count() == 2
    assert post_callback(job, {"event": "finished"}) == 204
    job.refresh_from_db()
    assert (job.status, job.result) == (Job.Status.COMPLETED, None)
    assert not job.batches.exists()
    assert Result.objects.filter(question__key="Q1").count() == assessment.students.count()


def test_a_failed_job_forgets_the_results_it_sent(
    assessment: Assessment, fully_marked: Assessment, user: User, idle_executor: None
) -> None:
    job = start_remark(assessment, "Q1", user)
    job.refresh_from_db()
    submissions = [
        submission.model_dump(mode="json") for submission in marked_by_sandbox(MarkingRun.model_validate(job.input))
    ]
    post_callback(job, {"event": "marked", "submissions": submissions[:1]})
    assert job.batches.exists()
    post_callback(job, {"event": "failed", "error": "Lost"})
    assert not job.batches.exists()


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
    job = Job.objects.get(kind=Job.Kind.PREVIEW)
    assert (job.status, job.question_keys) == (Job.Status.COMPLETED, ["Q1", "Q3"])
    answers = client.get(reverse("question_group_draft_answers", args=[assessment.pk, draft.pk])).content
    assert b"results change" in answers


def test_a_preview_counts_the_students_whose_marks_change(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["42"]})
    preview_draft(client, draft)
    preview_results = Job.objects.get(kind=Job.Kind.PREVIEW).result or []
    students = {student.encrypted_id: student for student in assessment.students.all()}
    for index, submission in enumerate(preview_results):
        result = MarkedSubmission.model_validate(submission).results["Q2"]
        proportion = 1 - result.proportion if index == 0 else result.proportion
        Result.objects.create(
            student=students[submission["student_id"]],
            question=question,
            proportion=proportion,
            feedback=result.feedback,
            method="Q",
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


def test_a_preview_says_when_answers_could_not_be_marked(client: Client, assessment: Assessment) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields | {"code": "raise ValueError('no such thing as results')"})
    draft = MarkingDraftRecord.objects.get()
    preview_draft(client, draft)
    question = assessment.questions.get(key="Q1")
    preview_results = [
        MarkedSubmission.model_validate(item) for item in Job.objects.get(kind=Job.Kind.PREVIEW).result or []
    ]
    unmarked_count = sum(not submission.results["Q1"].is_marked for submission in preview_results)
    assert unmarked_count
    page = client.get(reverse("answers", args=[assessment.pk, question.pk])).text
    preview_bar = page[page.index("preview-bar") :][:3000]
    assert f"Couldn't mark {unmarked_count} students" in preview_bar
    assert "no such thing as results" in preview_bar
    unmarked = [card for card in page.split('<details class="group/answer')[1:] if "Couldn't mark</span>" in card]
    assert unmarked, "groups the draft couldn't mark say so instead of showing marks"
    assert not any("line-through" in card[: card.index("</summary>")] for card in unmarked)


class CancellingExecutor(IdleExecutor):
    cancelled: ClassVar[list[uuid.UUID]] = []

    def cancel(self, job: Job) -> None:
        self.cancelled.append(job.pk)


def test_replaced_jobs_stop_once_their_replacement_is_saved(
    assessment: Assessment,
    fully_marked: Assessment,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    monkeypatch.setattr(queue, "executor", CancellingExecutor)
    CancellingExecutor.cancelled = []
    first = start_remark(assessment, "Q1", user)
    with django_capture_on_commit_callbacks(execute=True):
        remark(assessment, ["Q1"], user)
        assert CancellingExecutor.cancelled == []
    assert CancellingExecutor.cancelled == [first.pk]
    first.refresh_from_db()
    assert first.error == "Replaced by a newer run."


def backdate_as_running(*jobs: Job | None, days: float = 1) -> None:
    Job.objects.filter(pk__in=[job.pk for job in jobs if job]).update(
        status=Job.Status.RUNNING, started_at=timezone.now() - timedelta(days=days)
    )


def test_lost_jobs_are_not_running(
    assessment: Assessment, fully_marked: Assessment, user: User, monkeypatch: pytest.MonkeyPatch, idle_executor: None
) -> None:
    lost, running = start_remark(assessment, "Q1", user), start_remark(assessment, "Q2", user)
    backdate_as_running(lost)
    published: list[tuple[str, ...]] = []
    monkeypatch.setattr(events, "notify_change", lambda *names: published.append(names))
    with CaptureQueriesContext(connection) as queries:
        assert running_remarks(assessment) == [running]
    assert all(query["sql"].startswith("SELECT") for query in queries.captured_queries), "showing jobs changes none"
    assert published == []
    lost.refresh_from_db()
    assert lost.is_active


def test_starting_work_fails_lost_jobs_together(
    assessment: Assessment, fully_marked: Assessment, user: User, monkeypatch: pytest.MonkeyPatch, idle_executor: None
) -> None:
    lost = [
        start_remark(assessment, "Q1", user),
        start_grouping(assessment, assessment.questions.get(key="Q1"), ["a"], user),
    ]
    backdate_as_running(*lost)
    published: list[tuple[str, ...]] = []
    monkeypatch.setattr(events, "notify_change", lambda *names: published.append(names))
    queue.dispatch()
    remark(assessment, ["Q3"], user)
    failed = Job.objects.filter(error="Ran past its time limit.")
    assert sorted(failed.values_list("kind", "input")) == [("grouping", None), ("remark", None)]
    assert published[0] == tuple(sorted([topic("grouping", assessment.pk), topic("jobs", assessment.pk)])), "together"


def test_a_lost_job_counts_against_no_limit_and_holds_up_no_grouping(
    assessment: Assessment, fully_marked: Assessment, user: User, settings: Settings, idle_executor: None
) -> None:
    settings.GRADEFLOW_RUNNING_JOBS_PER_USER = 1
    question = assessment.questions.get(key="Q1")
    lost = start_grouping(assessment, question, ["a"], user)
    backdate_as_running(lost)
    grouping = start_grouping(assessment, question, ["a"], user)
    assert grouping != lost
    backdate_as_running(grouping)
    started = start_remark(assessment, "Q1", user)
    started.refresh_from_db()
    assert started.error == ""


def test_a_job_dispatched_but_never_started_times_out_after_the_limit(
    assessment: Assessment, fully_marked: Assessment, user: User, idle_executor: None
) -> None:
    stuck_job, recent = start_remark(assessment, "Q1", user), start_remark(assessment, "Q2", user)
    Job.objects.filter(pk=stuck_job.pk).update(dispatched_at=timezone.now() - timedelta(days=1))
    assert list(Job.objects.timed_out()) == [stuck_job]
    assert running_remarks(assessment) == [recent]
    queue.dispatch()
    stuck_job.refresh_from_db()
    assert (stuck_job.status, stuck_job.error) == (Job.Status.FAILED, "Ran past its time limit.")


def test_unreadable_marked_results_fail_the_job(
    assessment: Assessment, fully_marked: Assessment, user: User, idle_executor: None
) -> None:
    job = start_remark(assessment, "Q1", user)
    assert post_callback(job, {"event": "marked", "submissions": [{"student_id": "S1"}]}) == 204
    job.refresh_from_db()
    assert (job.status, job.error) == (Job.Status.FAILED, events.UNREADABLE_RESULTS_ERROR)
    assert not job.batches.exists()
    assert not Result.objects.filter(question__key="Q1").exists()


def test_remark_provenance_is_retained_after_a_day(
    assessment: Assessment, fully_marked: Assessment, user: User, idle_executor: None
) -> None:
    old, recent = start_remark(assessment, "Q1", user), start_remark(assessment, "Q2", user)
    Job.objects.filter(pk=old.pk).update(status=Job.Status.COMPLETED, finished_at=timezone.now() - timedelta(days=2))
    Job.objects.filter(pk=recent.pk).update(status=Job.Status.COMPLETED, finished_at=timezone.now())
    remark(assessment, ["Q3"], user)
    assert Job.objects.filter(pk=old.pk).exists()
    assert Job.objects.filter(pk=recent.pk).exists()


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
