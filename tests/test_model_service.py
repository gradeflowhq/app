from datetime import timedelta
from typing import cast

import pytest
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from gradeflow_core import GradeFlowError
from gradeflow_core.editing import ModelTrial, dump_yaml
from pytest_django import Settings

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs import queue
from gradeflow_app.jobs.models import Job
from gradeflow_app.marking.answers.status import notices
from gradeflow_app.marking.checks import checks
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.remarks import schedule_marking
from gradeflow_app.model_service import views
from gradeflow_app.model_service.endpoints import STATE_HEADER, State, endpoint
from gradeflow_app.requests import Request
from tests.conftest import Idle, call_back

URL = "https://models.example.com/v1"
PROVIDER = "Example AI"
# Built unchecked, as the server offers the provider only once `_provider` runs.
STATE = State.model_construct(provider=PROVIDER)
HEADERS = {
    "X-Model-Token": "secret-token",
    STATE_HEADER: State.model_construct(provider=PROVIDER, decision_model="decider").model_dump_json(),
}
TRIALS = [
    {"model": "decider", "role": "decision", "matched": 6, "total": 6, "seconds": 4.2, "failure": None},
    {
        "model": "tiny",
        "role": "decision",
        "matched": 0,
        "total": 6,
        "seconds": 2.0,
        "failure": "Missed 6 of 6 examples.",
    },
]
TRIED = [ModelTrial.model_validate(trial) for trial in TRIALS]
CRITERIA = {"criteria": [{"text": "Says ice floats", "marks": 1}]}


@pytest.fixture(autouse=True)
def _provider(settings: Settings) -> None:
    settings.GRADEFLOW_MODEL_PROVIDERS = {PROVIDER: URL}


@pytest.fixture
def idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queue, "executor", Idle)


def carrying(user: User) -> Request:
    """A request from `user`'s browser, carrying its model service's headers."""
    request = cast(Request, RequestFactory().post("/", headers=HEADERS))
    request.user = user
    return request


def post(client: Client, name: str, data: dict[str, str] | None = None, state: State = STATE) -> HttpResponse:
    """A model service request from a device connected to `state`'s endpoint."""
    return cast(
        HttpResponse,
        client.post(reverse(name), {**(data or {}), "model_state": state.model_dump_json()}, headers=HEADERS),
    )


def kept(response: HttpResponse) -> State:
    """What the response says the device now knows of its model service."""
    return State.model_validate_json(response[STATE_HEADER])


def criteria_marking(assessment: Assessment) -> Marking:
    question = assessment.questions.get(key="Q3")
    question.definition = {"type": "text", "max_marks": 1}
    question.save()
    definition = {"question": "Q3", "uses": "gradeflow/answer-criteria@1", "with": CRITERIA}
    return Marking.objects.create(assessment=assessment, question=question, definition=dump_yaml(definition))


def test_the_ai_service_is_among_the_accounts_integrations(client: Client) -> None:
    page = client.get(reverse("integrations")).content.decode()
    assert "<h1>Integrations</h1>" in page
    assert f"<option>{PROVIDER}</option>" in page
    assert 'id="ai-service-guide"' in page
    assert 'id="model-service"' not in client.get(reverse("settings")).content.decode()


def test_a_server_without_ai_services_says_so(client: Client, settings: Settings) -> None:
    settings.GRADEFLOW_MODEL_PROVIDERS = {}
    assert "This server offers no AI service." in client.get(reverse("integrations")).content.decode()


def test_connecting_offers_both_ways_to_choose(client: Client, idle: None) -> None:
    response = post(client, "model_service_connect")
    assert not Job.objects.exists()
    page = response.content.decode()
    assert "Not chosen yet." in page
    assert "Choose models" in page
    assert 'id="model-chooser"' not in page
    assert kept(response) == State(provider=PROVIDER)


def test_choosing_automatically_runs_a_job_with_the_token(client: Client, idle: None) -> None:
    response = post(client, "model_service_choose", {"how": "automatically"})
    job = Job.objects.get(kind=Job.Kind.MODEL_CHOICE)
    assert job.input == {
        "model_endpoint": {
            "url": URL,
            "token": "secret-token",
            "decision_model": None,
            "reasoning_model": None,
            "answers_per_request": 12,
        }
    }
    assert "secret-token" not in str(job.provenance)
    assert "Choosing automatically, about 10 minutes" in response.content.decode()


def test_choosing_myself_offers_the_endpoints_models_and_keeps_those_picked(
    client: Client, idle: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "list_models", lambda endpoint: ["decider", "reasoner"])
    offered = post(client, "model_service_chooser").content.decode()
    assert "<option>decider</option>" in offered
    response = post(client, "model_service_choose", {"how": "myself", "decision_model": "decider"})
    assert kept(response) == State(provider=PROVIDER, decision_model="decider", how="myself")
    assert "Fast <code>decider</code>" in response.content.decode()
    assert not Job.objects.exists()


def test_choosing_myself_says_why_the_service_lists_no_models(
    client: Client, idle: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def none(endpoint: object) -> list[str]:
        raise GradeFlowError("No models were found at this address. Check it: it often ends in /v1.")

    monkeypatch.setattr(views, "list_models", none)
    offered = post(client, "model_service_chooser").content.decode()
    assert "No models were found at this address" in offered


def test_only_the_servers_ai_services_connect(client: Client, user: User, idle: None) -> None:
    elsewhere = State.model_construct(provider="https://models.internal/v1")
    response = post(client, "model_service_connect", state=elsewhere)
    assert b"Choose an AI service and enter its token." in response.content
    assert b'data-refused="true"' in response.content
    assert STATE_HEADER not in response
    request = cast(Request, RequestFactory().post("/", headers={**HEADERS, STATE_HEADER: elsewhere.model_dump_json()}))
    assert endpoint(request) is None
    found = endpoint(carrying(user))
    assert found is not None
    assert found.url == URL


def test_the_device_collects_the_models_chosen_and_how_each_did(
    client: Client, idle: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    in_use = State(provider=PROVIDER, decision_model="earlier", how="myself")
    post(client, "model_service_choose", {"how": "automatically"}, state=in_use)
    assert kept(post(client, "model_service", state=in_use)) == in_use
    job = Job.objects.get(kind=Job.Kind.MODEL_CHOICE)
    choice = {"decision_model": "decider", "reasoning_model": None, "trials": TRIALS}
    for event in ({"event": "started"}, {"event": "chosen", "choice": choice}, {"event": "finished"}):
        assert call_back(job, event) == 204
    job.refresh_from_db()
    assert (job.status, job.input) == (Job.Status.COMPLETED, None)
    collected = kept(post(client, "model_service", state=in_use))
    assert (collected.decision_model, collected.how, collected.trials) == ("decider", "automatically", TRIED)
    assert kept(post(client, "model_service", state=collected)) == collected
    monkeypatch.setattr(views, "list_models", lambda endpoint: ["decider", "tiny"])
    dialog = post(client, "model_service_chooser", state=collected).content.decode()
    assert dialog.index("decider") < dialog.index("tiny")
    assert "In use" in dialog
    assert 'title="Missed 6 of 6 examples."' in dialog


def test_testing_the_models_picked_shows_how_they_did(client: Client, idle: None) -> None:
    picked = {"decision_model": "decider", "reasoning_model": ""}
    assert "Testing…" in post(client, "model_service_test", picked).content.decode()
    job = Job.objects.get(kind=Job.Kind.MODEL_TEST)
    assert job.input is not None
    assert job.input["tries"] == [{"model": "decider", "role": "decision"}]
    for event in ({"event": "started"}, {"event": "tried", "trials": TRIALS[:1]}, {"event": "finished"}):
        assert call_back(job, event) == 204
    response = post(client, "model_service_status", picked)
    assert "6 of 6 examples right" in response.content.decode()
    assert kept(response).trials == TRIED[:1]


def test_an_automatic_choice_that_failed_says_why(client: Client, idle: None) -> None:
    post(client, "model_service_choose", {"how": "automatically"})
    job = Job.objects.get(kind=Job.Kind.MODEL_CHOICE)
    call_back(job, {"event": "started"})
    call_back(job, {"event": "failed", "error": "No model got every example right."})
    page = post(client, "model_service").content.decode()
    assert "Couldn't choose automatically: No model got every example right." in page


def test_answers_per_request_are_kept_on_the_device_and_sent_with_jobs(client: Client, idle: None) -> None:
    response = post(client, "model_service_answers_per_request", {"answers_per_request": "6"})
    assert kept(response).answers_per_request == 6
    refused = post(client, "model_service_answers_per_request", {"answers_per_request": "41"})
    assert "Enter a number from 1 to 40." in refused.content.decode()
    assert kept(refused).answers_per_request == 12
    post(client, "model_service_choose", {"how": "automatically"}, state=kept(response))
    job = Job.objects.get(kind=Job.Kind.MODEL_CHOICE)
    assert job.input is not None
    assert job.input["model_endpoint"]["answers_per_request"] == 6


def test_stopping_ends_the_running_choice_without_calling_it_a_failure(client: Client, idle: None) -> None:
    post(client, "model_service_choose", {"how": "automatically"})
    page = post(client, "model_service_stop").content.decode()
    assert not Job.objects.filter(kind=Job.Kind.MODEL_CHOICE, status__in=Job.ACTIVE).exists()
    assert "Couldn't choose automatically" not in page
    assert "Not chosen yet." in page


def test_marking_that_asks_a_model_carries_the_endpoint_with_its_chosen_models(
    client: Client, user: User, assessment: Assessment, idle: None
) -> None:
    marking = criteria_marking(assessment)
    job = schedule_marking(marking, user, endpoint(carrying(user)))
    assert job is not None
    assert job.input is not None
    assert job.input["model_endpoint"]["token"] == "secret-token"
    assert job.input["model_endpoint"]["decision_model"] == "decider"


def test_marking_that_asks_no_model_leaves_the_token_out(
    client: Client, user: User, assessment: Assessment, idle: None
) -> None:
    question = assessment.questions.get(key="Q3")
    definition = {"question": "Q3", "uses": "gradeflow/full-marks@1"}
    marking = Marking.objects.create(assessment=assessment, question=question, definition=dump_yaml(definition))
    job = schedule_marking(marking, user, endpoint(carrying(user)))
    assert job is not None
    assert job.input is not None
    assert job.input["model_endpoint"] is None


def test_a_change_from_a_device_without_a_model_service_keeps_the_decisions(
    client: Client, user: User, assessment: Assessment, idle: None
) -> None:
    marking = criteria_marking(assessment)
    assert schedule_marking(marking, user) is None
    assert notices(checks(assessment), ["Q3"]) == [
        "Not marked again: the change came from a device without an AI service. The marks shown are from before."
    ]


def test_jobs_that_ask_a_model_get_longer(
    client: Client, user: User, assessment: Assessment, idle: None, settings: Settings
) -> None:
    settings.GRADEFLOW_MODEL_JOB_TIME_LIMIT = 1800
    asking = schedule_marking(criteria_marking(assessment), user, endpoint(carrying(user)))
    assert asking is not None
    assert asking.time_limit == 1800


def test_marking_says_what_it_waits_on(client: Client, user: User, assessment: Assessment, idle: None) -> None:
    job = schedule_marking(criteria_marking(assessment), user, endpoint(carrying(user)))
    assert job is not None
    call_back(job, {"event": "started"})
    assert call_back(job, {"event": "progress", "done": 12, "total": 30, "step": "Deciding criteria"}) == 204
    assert notices(checks(assessment), ["Q3"]) == [
        "Marking… Deciding criteria: 12 of 30. The marks shown are from before."
    ]


def test_the_answers_column_asks_to_connect_when_marking_asks_a_model(
    client: Client, user: User, assessment: Assessment, idle: None
) -> None:
    criteria_marking(assessment)
    question = assessment.questions.get(key="Q3")
    page = client.get(reverse("question", args=[assessment.pk, question.pk])).content.decode()
    assert "This marking uses AI" in page


def test_choosing_again_leaves_the_running_choice(client: Client, idle: None) -> None:
    post(client, "model_service_choose", {"how": "automatically"})
    post(client, "model_service_choose", {"how": "automatically"})
    assert Job.objects.filter(kind=Job.Kind.MODEL_CHOICE, status__in=Job.ACTIVE).count() == 1


def test_a_job_is_lost_past_its_own_time_limit(user: User, idle: None) -> None:
    started = timezone.now() - timedelta(seconds=700)
    short = Job.objects.create(
        kind=Job.Kind.MODEL_CHOICE, status=Job.Status.RUNNING, started_at=started, time_limit=600
    )
    long = Job.objects.create(
        kind=Job.Kind.MODEL_CHOICE, status=Job.Status.RUNNING, started_at=started, time_limit=1800
    )
    assert list(Job.objects.lost()) == [short]
    assert list(Job.objects.running()) == [long]


def test_choosing_without_a_connected_device_says_so(client: Client, idle: None) -> None:
    response = client.post(reverse("model_service_choose"), {"how": "myself", "decision_model": "decider"})
    assert "connected to an AI service." in response.content.decode()
    assert STATE_HEADER not in response
