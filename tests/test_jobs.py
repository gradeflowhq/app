import hashlib
import hmac
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
from gradeflow_app.core_build import CORE_BUILD
from gradeflow_app.jobs import events, queue
from gradeflow_app.jobs.executors import Nomad, Unavailable
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import Payload
from gradeflow_app.live.topics import topic
from gradeflow_app.marking.answers import comparisons
from gradeflow_app.marking.answers.column import PreviewSummary
from gradeflow_app.marking.answers.comparisons import cached_comparison, start_grouping
from gradeflow_app.marking.checks import tree_of
from gradeflow_app.marking.models import Draft
from gradeflow_app.marking.remarks import active_jobs, remark
from gradeflow_app.results.models import Result
from tests.conftest import HTMX, Idle, call_back, code_draft, full_marks, marked_by_sandbox, start_marking

pytestmark = pytest.mark.django_db


@pytest.fixture
def marked(assessment: Assessment) -> Assessment:
    """The assessment with every question marked, so marking them again runs a job."""
    return full_marks(assessment)


def test_drafts_preview_on_request(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, fields = code_draft(client, assessment)
    response = client.post(url, fields)
    assert not Job.objects.filter(kind=Job.Kind.PREVIEW).exists(), "changes don't preview by themselves"
    assert b'id="preview-bar"' in response.content
    preview = reverse("draft_preview", args=[assessment.pk, Draft.objects.get().pk])
    with django_capture_on_commit_callbacks(execute=True):
        client.post(preview)
    client.post(preview)
    assert Job.objects.filter(kind=Job.Kind.PREVIEW).count() == 1, "previewing an unchanged draft again adds none"
    job = Job.objects.get(kind=Job.Kind.PREVIEW)
    answers = {student.key for student in assessment.students.all()}
    assert job.status == Job.Status.COMPLETED
    assert {item["student_id"] for item in job.result or []} == answers
    client.post(url, fields | {"code": fields["code"] + "\n# changed"})
    client.post(preview)
    assert Job.objects.filter(kind=Job.Kind.PREVIEW).count() == 2


def test_a_preview_another_build_of_the_core_ran_is_run_again(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields)
    preview = reverse("draft_preview", args=[assessment.pk, Draft.objects.get().pk])
    with django_capture_on_commit_callbacks(execute=True):
        client.post(preview)
    Job.objects.filter(kind=Job.Kind.PREVIEW).update(core_build="an earlier build", result=[{"old": "shape"}])
    assert client.get(url).status_code == 200, "its results aren't read"
    client.post(preview)
    assert Job.objects.filter(kind=Job.Kind.PREVIEW).count() == 2


@pytest.mark.parametrize("ending", ["draft_save", "draft_discard"])
def test_saving_or_discarding_a_draft_stops_its_preview(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields)
    monkeypatch.setattr(queue, "executor", Idle)
    draft = Draft.objects.get()
    client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    preview = Job.objects.get(kind=Job.Kind.PREVIEW)
    assert preview.active
    client.post(reverse(ending, args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    preview.refresh_from_db()
    assert not preview.active


def test_pages_follow_jobs_as_they_run(
    client: Client, assessment: Assessment, marked: Assessment, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields)
    monkeypatch.setattr(queue, "executor", Idle)
    heard: list[str] = []
    monkeypatch.setattr(events, "publish", lambda *names: heard.extend(names))
    client.post(reverse("draft_preview", args=[assessment.pk, Draft.objects.get().pk]))
    job = Job.objects.get(kind=Job.Kind.PREVIEW)
    call_back(job, {"event": "started"})
    call_back(job, {"event": "finished"})
    assert heard == [topic("preview", assessment.pk, user.pk)] * 3, "a preview concerns only whoever asked for it"
    heard.clear()
    remarking = remarked(assessment, "Q1", user)
    call_back(remarking, {"event": "failed", "error": "Lost"})
    assert heard == [topic("jobs", assessment.pk)] * 2, "marks change only once a remark completes"


def test_a_user_may_run_only_so_many_jobs_at_once(
    assessment: Assessment, marked: Assessment, user: User, settings: Settings
) -> None:
    settings.GRADEFLOW_JOBS_PER_USER = 1
    start_grouping(assessment, assessment.questions.get(key="Q1"), ["light"], user)
    refused = remarked(assessment, "Q1", user)
    refused.refresh_from_db()
    assert refused.status == Job.Status.QUEUED
    assert refused.error == ""


def remarked(assessment: Assessment, key: str, user: User) -> Job:
    """The job marking `key` again."""
    remark(assessment, [key], user)
    return assessment.jobs.filter(kind=Job.Kind.REMARK).latest("created_at")


def comparison_for(texts: list[str]) -> AnswerComparison:
    return compare_answers(texts, similar="exact").model_copy(update={"similar": "meaning"})


def compared(
    comparison: AnswerComparison, *, index: int = 0, total: int = 1, part: str | None = None
) -> dict[str, Any]:
    return {
        "event": "compared",
        "index": index,
        "total": total,
        "part": comparison.model_dump_json() if part is None else part,
    }


def test_grouping_caches_a_complete_comparison_only_after_success(
    assessment: Assessment, user: User, settings: Settings
) -> None:
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    texts = ["dark", "light"]
    comparison = comparison_for(texts)
    job = start_grouping(assessment, assessment.questions.get(key="Q1"), texts, user)
    signature = hmac.new(job.secret.encode(), b"input", hashlib.sha256).hexdigest()
    sent = Client().get(reverse("job_input", args=[job.pk]), headers={"X-GradeFlow-Signature": signature})
    assert sent.json() == {"texts": texts}
    assert job.core_build == CORE_BUILD
    payload = comparison.model_dump_json()
    first = compared(comparison, total=2, part=payload[:10])
    assert call_back(job, first) == 204
    assert call_back(job, first) == 204, "retries do not append the same part twice"
    job.refresh_from_db()
    assert job.progress == 0.5
    assert cached_comparison(texts) is None
    assert call_back(job, compared(comparison, index=1, total=2, part=payload[10:])) == 204
    assert cached_comparison(texts) is None
    assert call_back(job, {"event": "finished"}) == 204
    job.refresh_from_db()
    assert (job.status, job.input, job.result) == (Job.Status.COMPLETED, None, None)
    assert cached_comparison(texts) == comparison
    assert call_back(job, first) == 204, "late callbacks leave a completed job alone"
    job.refresh_from_db()
    assert job.result is None


def test_a_comparison_is_cached_under_its_captured_build(
    assessment: Assessment, user: User, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    monkeypatch.setattr(queue, "CORE_BUILD", "captured-grouping-build")
    texts = ["captured grouping answer"]
    comparison = comparison_for(texts)
    job = start_grouping(assessment, assessment.questions.get(key="Q1"), texts, user)
    assert call_back(job, compared(comparison)) == 204
    assert call_back(job, {"event": "finished"}) == 204
    assert cached_comparison(texts) is None
    monkeypatch.setattr(comparisons, "CORE_BUILD", job.core_build)
    assert cached_comparison(texts) == comparison


@pytest.mark.parametrize("outcome", ["failed", "absent", "missing", "invalid", "wrong_texts", "wrong_mode"])
def test_unsuccessful_comparisons_never_enter_the_cache(
    assessment: Assessment, user: User, settings: Settings, outcome: str
) -> None:
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    texts = [f"uncached {outcome}"]
    comparison = comparison_for(texts)
    job = start_grouping(assessment, assessment.questions.get(key="Q1"), texts, user)
    if outcome == "wrong_texts":
        comparison = comparison_for(["different answer"])
    elif outcome == "wrong_mode":
        comparison = compare_answers(texts, similar="exact")
    event = compared(comparison, total=2 if outcome == "missing" else 1)
    if outcome == "invalid":
        event["part"] = "not JSON"
    if outcome != "absent":
        assert call_back(job, event) == 204
    assert call_back(job, {"event": "failed" if outcome == "failed" else "finished", "error": "Lost"}) == 204
    job.refresh_from_db()
    assert job.status == Job.Status.FAILED
    assert job.result is None
    assert cached_comparison(texts) is None


def test_comparison_parts_must_arrive_in_order_and_duplicates_must_match(assessment: Assessment, user: User) -> None:
    texts = ["part validation"]
    comparison = comparison_for(texts)
    job = start_grouping(assessment, assessment.questions.get(key="Q1"), texts, user)
    assert call_back(job, compared(comparison, index=1, total=2)) == 400
    assert call_back(job, compared(comparison, total=0)) == 400
    assert call_back(job, {"event": "compared"}) == 400
    assert call_back(job, compared(comparison, total=2, part="first")) == 204
    assert call_back(job, compared(comparison, total=2, part="different")) == 400
    assert call_back(job, compared(comparison, index=1, total=3, part="next")) == 400


def test_changed_grouping_inputs_replace_the_pending_job(assessment: Assessment, user: User) -> None:
    question = assessment.questions.get(key="Q1")
    first = start_grouping(assessment, question, ["old"], user)
    assert start_grouping(assessment, question, ["old"], user) == first
    assert call_back(first, compared(comparison_for(["old"]), total=2, part="{")) == 204
    second = start_grouping(assessment, question, ["new"], user)
    assert second != first
    first.refresh_from_db()
    assert first.status == Job.Status.FAILED
    assert call_back(first, compared(comparison_for(["old"]))) == 204
    first.refresh_from_db()
    assert first.result is None
    assert second.input == {"texts": ["new"]}


def test_grouping_by_meaning_starts_from_the_page_and_not_on_reading_it(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    answers = reverse("answers", args=[assessment.pk, question.pk])
    start = reverse("answer_grouping", args=[assessment.pk, question.pk])
    due = client.get(answers, {"group": "meaning"}, headers=HTMX).content.decode()
    assert not Job.objects.filter(kind=Job.Kind.GROUPING).exists()
    assert f'hx-post="{start}" hx-trigger="load"' in due
    waiting = client.post(start, headers=HTMX).content.decode()
    assert "Grouping by meaning." in waiting
    assert 'hx-trigger="load"' not in waiting
    client.post(start, headers=HTMX)
    job = Job.objects.get(kind=Job.Kind.GROUPING)
    assert call_back(job, {"event": "failed", "error": "Lost"}) == 204
    failed = client.get(answers, headers=HTMX).content.decode()
    assert "Couldn't group by meaning" in failed
    assert f'hx-post="{start}" hx-target="#answers"' in failed, "trying again"
    assert 'hx-trigger="load"' not in failed
    client.post(start, headers=HTMX)
    assert Job.objects.filter(kind=Job.Kind.GROUPING, status=Job.Status.QUEUED).count() == 1


def test_failed_grouping_of_older_answers_does_not_block_current_answers(
    client: Client, assessment: Assessment
) -> None:
    question = assessment.questions.get(key="Q1")
    answers = reverse("answers", args=[assessment.pk, question.pk])
    start = reverse("answer_grouping", args=[assessment.pk, question.pk])
    client.get(answers, {"group": "meaning"}, headers=HTMX)
    client.post(start, headers=HTMX)
    job = Job.objects.get(kind=Job.Kind.GROUPING)
    assert call_back(job, {"event": "failed", "error": "Lost"}) == 204
    student = assessment.students.first()
    assert student is not None
    student.answers = student.answers | {"Q1": "A newly imported answer"}
    student.save(update_fields=["answers"])
    due = client.get(answers, headers=HTMX).content.decode()
    assert "Couldn't group by meaning" not in due
    assert f'hx-post="{start}" hx-trigger="load"' in due
    client.post(start, headers=HTMX)
    assert Job.objects.filter(kind=Job.Kind.GROUPING, status=Job.Status.QUEUED).count() == 1


def test_callbacks_are_signed_and_fit_their_job(assessment: Assessment, marked: Assessment, user: User) -> None:
    job = start_grouping(assessment, assessment.questions.get(key="Q1"), ["light"], user)
    assert call_back(job, {"event": "finished"}, secret="forged") == 403
    remarking = remarked(assessment, "Q1", user)
    assert call_back(remarking, {"event": "compared", "index": 0, "total": 1, "part": "{}"}) == 400


def test_nomad_runs_each_kind_of_job_in_the_image_it_recorded(settings: Settings) -> None:
    settings.GRADEFLOW_JOB_URL = "http://172.17.0.2:8001"
    nomad = Nomad(url="http://nomad", token="", datacenter="dc1", namespace="gradeflow")
    for kind, task, priority in (
        (Job.Kind.GROUPING, "compare", 70),
        (Job.Kind.REMARK, "mark", 50),
        (Job.Kind.PREVIEW, "mark", 70),
        (Job.Kind.DRAFT_TESTS, "test", 70),
        (Job.Kind.VERSION_TESTS, "test", 70),
    ):
        job = Job(pk=uuid.uuid7(), kind=kind, image="localhost:5055/sandbox@sha256:" + "a" * 64, time_limit=60)
        spec: Any = nomad.spec(job)
        sandbox = spec["TaskGroups"][0]["Tasks"][0]
        assert sandbox["Config"]["image"] == job.image
        assert [sandbox["Config"]["command"], *sandbox["Config"]["args"]] == ["python", "/app/sandbox.py", task, "60"]
        assert sandbox["Env"]["GRADEFLOW_JOB_URL"] == f"http://172.17.0.2:8001/jobs/{job.pk}/"
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

    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.params["prefix"] == "gradeflow-"
        assert request.url.params["namespace"] == "gradeflow"
        return httpx.Response(200, json=listed)

    nomad.client = httpx.Client(base_url="http://nomad", transport=httpx.MockTransport(answer))
    assert nomad.received() == {ended: True, running: False}
    nomad.client = httpx.Client(base_url="http://nomad", transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    with pytest.raises(Unavailable):
        nomad.received()
    with pytest.raises(Unavailable):
        nomad.submit(Job(pk=ended, kind=Job.Kind.REMARK))


def test_results_arrive_in_batches_which_show_progress(
    client: Client, assessment: Assessment, marked: Assessment, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue, "executor", Idle)
    job = remarked(assessment, "Q1", user)
    job.refresh_from_db()
    submissions = [
        submission.model_dump(mode="json") for submission in marked_by_sandbox(Payload.model_validate(job.input))
    ]
    half = len(submissions) // 2
    assert call_back(job, {"event": "started"}) == 204
    assert call_back(job, {"event": "marked", "submissions": submissions[:half]}) == 204
    job.refresh_from_db()
    assert job.progress == half / len(submissions)
    listed = client.get(reverse("question_list", args=[assessment.pk]), {"shown": ""}).content.decode()
    assert f'value="{job.progress:.2f}" max="1"' in listed
    question = assessment.questions.get(key="Q1")
    column = client.get(reverse("answers", args=[assessment.pk, question.pk])).content.decode()
    assert "Marking…" in column
    assert f'value="{job.progress:.2f}" max="1"' in column
    assert call_back(job, {"event": "marked", "submissions": submissions[half:]}) == 204
    assert job.batches.count() == 2
    assert call_back(job, {"event": "finished"}) == 204
    job.refresh_from_db()
    assert (job.status, job.result) == (Job.Status.COMPLETED, None)
    assert not job.batches.exists()
    assert Result.objects.filter(question__key="Q1").count() == assessment.students.count()


def test_a_failed_job_forgets_the_results_it_sent(
    assessment: Assessment, marked: Assessment, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue, "executor", Idle)
    job = remarked(assessment, "Q1", user)
    job.refresh_from_db()
    submissions = [
        submission.model_dump(mode="json") for submission in marked_by_sandbox(Payload.model_validate(job.input))
    ]
    call_back(job, {"event": "marked", "submissions": submissions[:1]})
    assert job.batches.exists()
    call_back(job, {"event": "failed", "error": "Lost"})
    assert not job.batches.exists()


def test_marking_questions_together_previews_on_request(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    created = client.post(
        reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/custom-code-for-a-group@1"}
    )
    page = created.headers["Location"]
    draft = Draft.objects.get()
    definition = tree_of(draft.definition)
    definition["with"]["questions"] = ["Q1", "Q3"]
    draft.definition = dump_yaml(definition)
    draft.save()
    column = client.get(page).content
    assert b'id="preview-bar"' in column
    assert b"Q1, Q3" in column, "the answers column covers both questions"
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    job = Job.objects.get(kind=Job.Kind.PREVIEW)
    assert (job.status, job.questions) == (Job.Status.COMPLETED, ["Q1", "Q3"])
    answers = client.get(reverse("question_group_draft_answers", args=[assessment.pk, draft.pk])).content
    assert b"results change" in answers


def test_a_preview_counts_the_students_whose_marks_change(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    question = assessment.questions.get(key="Q2")
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields": "1", "answers": ["42"]})
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    previewed = Job.objects.get(kind=Job.Kind.PREVIEW).result or []
    students = {student.key: student for student in assessment.students.all()}
    for index, item in enumerate(previewed):
        result = MarkedSubmission.model_validate(item).results["Q2"]
        proportion = 1 - result.proportion if index == 0 else result.proportion
        Result.objects.create(
            student=students[item["student_id"]],
            question=question,
            proportion=proportion,
            feedback=result.feedback,
            method="Q",
            revision=0,
        )
    page = client.get(reverse("answers", args=[assessment.pk, question.pk])).content.decode()
    bar = page[page.index("preview-bar") :][:1500]
    assert "1 changed" in bar, bar


def test_editing_back_to_the_previewed_marking_shows_its_preview_again(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    previewed = {"path": "", "fields": "1", "points": ["light"], "combine": "all"}
    client.post(url, previewed)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    assert "Marking changed" in client.post(url, previewed | {"combine": "any"}).content.decode()
    back = client.post(url, previewed).content.decode()
    bar = back[back.index('id="preview-bar"') :][:1500]
    assert "Marking changed" not in bar
    assert " full · " in bar, bar


def test_a_preview_says_when_answers_could_not_be_marked(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields | {"code": "raise ValueError('no such thing as results')"})
    draft = Draft.objects.get()
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    question = assessment.questions.get(key="Q1")
    previewed = [MarkedSubmission.model_validate(item) for item in Job.objects.get(kind=Job.Kind.PREVIEW).result or []]
    failed = sum(not submission.results["Q1"].marked for submission in previewed)
    assert failed
    page = client.get(reverse("answers", args=[assessment.pk, question.pk])).content.decode()
    bar = page[page.index("preview-bar") :][:3000]
    assert f"Couldn't mark {failed} students" in bar
    assert "no such thing as results" in bar
    unmarked = [card for card in page.split('<details class="group/answer')[1:] if "Couldn't mark</span>" in card]
    assert unmarked, "groups the draft couldn't mark say so instead of showing marks"
    assert not any("line-through" in card[: card.index("</summary>")] for card in unmarked)


class Cancelling(Idle):
    cancelled: ClassVar[list[uuid.UUID]] = []

    def cancel(self, job: Job) -> None:
        self.cancelled.append(job.pk)


def test_replaced_jobs_stop_once_their_replacement_is_saved(
    assessment: Assessment,
    marked: Assessment,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    monkeypatch.setattr(queue, "executor", Cancelling)
    Cancelling.cancelled = []
    first = remarked(assessment, "Q1", user)
    with django_capture_on_commit_callbacks(execute=True):
        remark(assessment, ["Q1"], user)
        assert Cancelling.cancelled == []
    assert Cancelling.cancelled == [first.pk]
    first.refresh_from_db()
    assert first.error == "Replaced by a newer run."


def lose(*lost: Job | None, days: float = 1) -> None:
    Job.objects.filter(pk__in=[job.pk for job in lost if job]).update(
        status=Job.Status.RUNNING, started_at=timezone.now() - timedelta(days=days)
    )


def test_lost_jobs_are_not_running(
    assessment: Assessment, marked: Assessment, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue, "executor", Idle)
    lost, running = remarked(assessment, "Q1", user), remarked(assessment, "Q2", user)
    lose(lost)
    published: list[tuple[str, ...]] = []
    monkeypatch.setattr(events, "publish", lambda *names: published.append(names))
    with CaptureQueriesContext(connection) as queries:
        assert active_jobs(assessment) == [running]
    assert all(query["sql"].startswith("SELECT") for query in queries.captured_queries), "showing jobs changes none"
    assert published == []
    lost.refresh_from_db()
    assert lost.active


def test_starting_work_fails_lost_jobs_together(
    assessment: Assessment, marked: Assessment, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue, "executor", Idle)
    lost = [
        remarked(assessment, "Q1", user),
        start_grouping(assessment, assessment.questions.get(key="Q1"), ["a"], user),
    ]
    lose(*lost)
    published: list[tuple[str, ...]] = []
    monkeypatch.setattr(events, "publish", lambda *names: published.append(names))
    queue.dispatch()
    remark(assessment, ["Q3"], user)
    failed = Job.objects.filter(error="Took too long.")
    assert sorted(failed.values_list("kind", "input")) == [("grouping", None), ("remark", None)]
    assert published[0] == tuple(sorted([topic("grouping", assessment.pk), topic("jobs", assessment.pk)])), "together"


def test_a_lost_job_counts_against_no_limit_and_holds_up_no_grouping(
    assessment: Assessment, marked: Assessment, user: User, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue, "executor", Idle)
    settings.GRADEFLOW_JOBS_PER_USER = 1
    question = assessment.questions.get(key="Q1")
    lost = start_grouping(assessment, question, ["a"], user)
    lose(lost)
    grouping = start_grouping(assessment, question, ["a"], user)
    assert grouping != lost
    lose(grouping)
    started = remarked(assessment, "Q1", user)
    started.refresh_from_db()
    assert started.error == ""


def test_a_job_admitted_but_never_started_is_lost_after_the_limit(
    assessment: Assessment, marked: Assessment, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue, "executor", Idle)
    waiting, recent = remarked(assessment, "Q1", user), remarked(assessment, "Q2", user)
    Job.objects.filter(pk=waiting.pk).update(admitted_at=timezone.now() - timedelta(days=1))
    assert list(Job.objects.lost()) == [waiting]
    assert active_jobs(assessment) == [recent]
    queue.dispatch()
    waiting.refresh_from_db()
    assert (waiting.status, waiting.error) == (Job.Status.FAILED, "Took too long.")


def test_unreadable_marked_results_fail_the_job(
    assessment: Assessment, marked: Assessment, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue, "executor", Idle)
    job = remarked(assessment, "Q1", user)
    assert call_back(job, {"event": "marked", "submissions": [{"student_id": "S1"}]}) == 204
    job.refresh_from_db()
    assert (job.status, job.error) == (Job.Status.FAILED, events.UNREADABLE)
    assert not job.batches.exists()
    assert not Result.objects.filter(question__key="Q1").exists()


def test_remark_provenance_is_retained_after_a_day(
    assessment: Assessment, marked: Assessment, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue, "executor", Idle)
    old, recent = remarked(assessment, "Q1", user), remarked(assessment, "Q2", user)
    Job.objects.filter(pk=old.pk).update(status=Job.Status.COMPLETED, finished_at=timezone.now() - timedelta(days=2))
    Job.objects.filter(pk=recent.pk).update(status=Job.Status.COMPLETED, finished_at=timezone.now())
    remark(assessment, ["Q3"], user)
    assert Job.objects.filter(pk=old.pk).exists()
    assert Job.objects.filter(pk=recent.pk).exists()


def test_a_preview_across_questions_counts_students_plainly() -> None:
    draft = SimpleNamespace(assessment_id=uuid.uuid7(), pk=uuid.uuid7())
    for changed, words in ((1, "1 student's results change"), (2, "2 students' results change")):
        summary = PreviewSummary(changed=changed, unmarked=0, problem="")
        bar = render_to_string(
            "results/answers.html#preview_bar", {"draft": draft, "preview_summary": summary, "across": True}
        )
        assert words in bar.replace("&#x27;", "'")
