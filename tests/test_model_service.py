from dataclasses import asdict
from datetime import timedelta
from typing import cast

import pytest
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from gradeflow_core import GradeFlowError, Progress
from gradeflow_core.editing import ModelTrial, dump_yaml
from pytest_django import Settings

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs.models import Job
from gradeflow_app.marking.answers.out_of_date import out_of_date_notices
from gradeflow_app.marking.checks import assessment_checks
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.remarks import schedule_marking
from gradeflow_app.model_service import views
from gradeflow_app.model_service.endpoints import STATE_HEADER, ModelServiceState, endpoint
from gradeflow_app.requests import Request
from tests.conftest import full_marks_marking, post_callback

PROVIDER_URL = "https://models.example.com/v1"
PROVIDER = "Example AI"
# Built unchecked, as the server offers the provider only once `_offer_provider` runs.
PROVIDER_STATE = ModelServiceState.model_construct(provider=PROVIDER)
MODEL_SERVICE_HEADERS = {
    "X-Model-Token": "secret-token",
    STATE_HEADER: ModelServiceState.model_construct(provider=PROVIDER, fast_model="decider").model_dump_json(),
}
TRIALS = [
    {"model": "decider", "role": "fast", "rows_matched": 6, "rows_tried": 6, "seconds": 4.2, "failure": None},
    {
        "model": "tiny",
        "role": "fast",
        "rows_matched": 0,
        "rows_tried": 6,
        "seconds": 2.0,
        "failure": "Missed 6 of 6 example answers.",
    },
]
MODEL_TRIALS = [ModelTrial.model_validate(trial) for trial in TRIALS]
CRITERIA = {"criteria": [{"criterion": "Says ice floats", "marks": 1}]}


@pytest.fixture(autouse=True)
def _offer_provider(settings: Settings) -> None:
    settings.GRADEFLOW_MODEL_PROVIDERS = {PROVIDER: PROVIDER_URL}


def request_with_model_service(user: User) -> Request:
    """A request from `user`'s browser, carrying its model service's headers."""
    request = cast(Request, RequestFactory().post("/", headers=MODEL_SERVICE_HEADERS))
    request.user = user
    return request


def post_model_service(
    client: Client, name: str, data: dict[str, str] | None = None, state: ModelServiceState = PROVIDER_STATE
) -> HttpResponse:
    """A model service request from a device connected to `state`'s endpoint."""
    return cast(
        HttpResponse,
        client.post(
            reverse(name), {**(data or {}), "model_state": state.model_dump_json()}, headers=MODEL_SERVICE_HEADERS
        ),
    )


def returned_state(response: HttpResponse) -> ModelServiceState:
    """What the response says the device now knows of its model service."""
    return ModelServiceState.model_validate_json(response[STATE_HEADER])


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


def test_connecting_offers_both_ways_to_choose(
    client: Client, idle_executor: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "list_models", lambda endpoint: ["decider"])
    response = post_model_service(client, "model_service_connect")
    assert not Job.objects.exists()
    page = response.content.decode()
    assert "Not chosen yet." in page
    assert "Choose models" in page
    assert 'id="model-chooser"' not in page
    assert returned_state(response) == ModelServiceState(provider=PROVIDER)


def test_choosing_automatically_runs_a_job_with_the_token(client: Client, idle_executor: None) -> None:
    response = post_model_service(client, "model_service_choose", {"chosen_by": "automatically"})
    job = Job.objects.get(kind=Job.Kind.MODEL_CHOICE)
    assert job.input == {
        "model_endpoint": {
            "url": PROVIDER_URL,
            "token": "secret-token",
            "fast_model": None,
            "careful_model": None,
            "answers_per_request": 12,
        }
    }
    assert "secret-token" not in str(job.marked_with)
    assert "Choosing automatically, about 10 minutes" in response.content.decode()


def test_choosing_myself_offers_the_endpoints_models_and_keeps_those_picked(
    client: Client, idle_executor: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "list_models", lambda endpoint: ["decider", "reasoner"])
    offered = post_model_service(client, "model_service_dialog").content.decode()
    assert "<option>decider</option>" in offered
    response = post_model_service(client, "model_service_choose", {"chosen_by": "myself", "fast_model": "decider"})
    assert returned_state(response) == ModelServiceState(provider=PROVIDER, fast_model="decider", chosen_by="myself")
    assert "Fast <code>decider</code>" in response.content.decode()
    assert not Job.objects.exists()


def test_choosing_myself_says_why_the_service_lists_no_models(
    client: Client, idle_executor: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_models(endpoint: object) -> list[str]:
        raise GradeFlowError("No models were found at this address. Check it: it often ends in /v1.")

    monkeypatch.setattr(views, "list_models", no_models)
    offered = post_model_service(client, "model_service_dialog").content.decode()
    assert "No models were found at this address" in offered


def test_connecting_refuses_a_token_the_service_refuses(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse_token(endpoint: object) -> list[str]:
        raise GradeFlowError("The AI service refused the token.")

    monkeypatch.setattr(views, "list_models", refuse_token)
    response = post_model_service(client, "model_service_connect")
    assert b"The AI service refused the token." in response.content
    assert f"<option selected>{PROVIDER}</option>".encode() in response.content
    assert b'data-refused="true"' in response.content
    assert STATE_HEADER not in response


def test_only_the_servers_ai_services_connect(client: Client, user: User, idle_executor: None) -> None:
    unoffered_state = ModelServiceState.model_construct(provider="https://models.internal/v1")
    response = post_model_service(client, "model_service_connect", state=unoffered_state)
    assert b"Choose an AI service and enter its token." in response.content
    assert b'data-refused="true"' in response.content
    assert STATE_HEADER not in response
    request = cast(
        Request,
        RequestFactory().post("/", headers={**MODEL_SERVICE_HEADERS, STATE_HEADER: unoffered_state.model_dump_json()}),
    )
    assert endpoint(request) is None
    model_endpoint = endpoint(request_with_model_service(user))
    assert model_endpoint is not None
    assert model_endpoint.url == PROVIDER_URL


def test_the_device_collects_the_models_chosen_and_how_each_did(
    client: Client, idle_executor: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_state = ModelServiceState(provider=PROVIDER, fast_model="earlier", chosen_by="myself")
    post_model_service(client, "model_service_choose", {"chosen_by": "automatically"}, state=current_state)
    assert returned_state(post_model_service(client, "model_service", state=current_state)) == current_state
    job = Job.objects.get(kind=Job.Kind.MODEL_CHOICE)
    choice = {"fast_model": "decider", "careful_model": None, "trials": TRIALS}
    for event in ({"event": "started"}, {"event": "chosen", "choice": choice}, {"event": "finished"}):
        assert post_callback(job, event) == 204
    job.refresh_from_db()
    assert (job.status, job.input) == (Job.Status.COMPLETED, None)
    collected = returned_state(post_model_service(client, "model_service", state=current_state))
    assert (collected.fast_model, collected.chosen_by, collected.trials) == ("decider", "automatically", MODEL_TRIALS)
    assert returned_state(post_model_service(client, "model_service", state=collected)) == collected
    monkeypatch.setattr(views, "list_models", lambda endpoint: ["decider", "tiny"])
    dialog = post_model_service(client, "model_service_dialog", state=collected).content.decode()
    assert dialog.index("decider") < dialog.index("tiny")
    assert "In use" in dialog
    assert 'title="Missed 6 of 6 example answers."' in dialog


def test_testing_the_models_picked_shows_how_they_did(client: Client, idle_executor: None) -> None:
    picked = {"fast_model": "decider", "careful_model": ""}
    assert "Testing…" in post_model_service(client, "model_service_test", picked).content.decode()
    job = Job.objects.get(kind=Job.Kind.MODEL_TRIAL)
    assert job.input is not None
    assert job.input["trials"] == [{"model": "decider", "role": "fast"}]
    for event in ({"event": "started"}, {"event": "trials", "trials": TRIALS[:1]}, {"event": "finished"}):
        assert post_callback(job, event) == 204
    response = post_model_service(client, "model_service_status", picked)
    assert "6 of 6 examples right" in response.content.decode()
    assert returned_state(response).trials == MODEL_TRIALS[:1]


def test_an_automatic_choice_that_failed_says_why(client: Client, idle_executor: None) -> None:
    post_model_service(client, "model_service_choose", {"chosen_by": "automatically"})
    job = Job.objects.get(kind=Job.Kind.MODEL_CHOICE)
    post_callback(job, {"event": "started"})
    post_callback(job, {"event": "failed", "error": "No model got every example right."})
    page = post_model_service(client, "model_service").content.decode()
    assert "Couldn't choose automatically: No model got every example right." in page


def test_answers_per_request_are_kept_on_the_device_and_sent_with_jobs(client: Client, idle_executor: None) -> None:
    response = post_model_service(client, "model_service_answers_per_request", {"answers_per_request": "6"})
    assert returned_state(response).answers_per_request == 6
    refused = post_model_service(client, "model_service_answers_per_request", {"answers_per_request": "41"})
    assert "Enter a number from 1 to 40." in refused.content.decode()
    assert returned_state(refused).answers_per_request == 12
    post_model_service(client, "model_service_choose", {"chosen_by": "automatically"}, state=returned_state(response))
    job = Job.objects.get(kind=Job.Kind.MODEL_CHOICE)
    assert job.input is not None
    assert job.input["model_endpoint"]["answers_per_request"] == 6


def test_stopping_ends_the_running_choice_without_calling_it_a_failure(client: Client, idle_executor: None) -> None:
    post_model_service(client, "model_service_choose", {"chosen_by": "automatically"})
    page = post_model_service(client, "model_service_stop").content.decode()
    assert not Job.objects.filter(kind=Job.Kind.MODEL_CHOICE, status__in=Job.ACTIVE).exists()
    assert "Couldn't choose automatically" not in page
    assert "Not chosen yet." in page


def test_marking_that_asks_a_model_carries_the_endpoint_with_its_chosen_models(
    client: Client, user: User, assessment: Assessment, idle_executor: None
) -> None:
    marking = criteria_marking(assessment)
    job = schedule_marking(marking, user, endpoint(request_with_model_service(user)))
    assert job is not None
    assert job.input is not None
    assert job.input["model_endpoint"]["token"] == "secret-token"
    assert job.input["model_endpoint"]["fast_model"] == "decider"


def test_marking_that_asks_no_model_leaves_the_token_out(
    client: Client, user: User, assessment: Assessment, idle_executor: None
) -> None:
    job = schedule_marking(full_marks_marking(assessment, "Q3"), user, endpoint(request_with_model_service(user)))
    assert job is not None
    assert job.input is not None
    assert job.input["model_endpoint"] is None


def test_a_change_from_a_device_without_a_model_service_keeps_the_decisions(
    client: Client, user: User, assessment: Assessment, idle_executor: None
) -> None:
    marking = criteria_marking(assessment)
    assert schedule_marking(marking, user) is None
    assert out_of_date_notices(assessment_checks(assessment), ["Q3"]) == [
        "Not marked again: the change came from a device without an AI service. The marks shown are from before."
    ]


def test_jobs_that_ask_a_model_get_longer(
    client: Client, user: User, assessment: Assessment, idle_executor: None, settings: Settings
) -> None:
    settings.GRADEFLOW_MODEL_JOB_TIME_LIMIT = 1800
    job = schedule_marking(criteria_marking(assessment), user, endpoint(request_with_model_service(user)))
    assert job is not None
    assert job.time_limit == 1800


def test_marking_says_what_it_waits_on(client: Client, user: User, assessment: Assessment, idle_executor: None) -> None:
    job = schedule_marking(criteria_marking(assessment), user, endpoint(request_with_model_service(user)))
    assert job is not None
    post_callback(job, {"event": "started"})
    progress = Progress(completed=12, total=30, step="Deciding criteria")
    assert post_callback(job, {"event": "progress", **asdict(progress)}) == 204
    assert out_of_date_notices(assessment_checks(assessment), ["Q3"]) == []
    question = assessment.questions.get(key="Q3")
    column = client.get(reverse("answers", args=[assessment.pk, question.pk])).content.decode()
    assert "Deciding criteria: 12 of 30" in column
    assert "from before" not in column


def test_the_answers_column_asks_to_connect_when_marking_asks_a_model(
    client: Client, user: User, assessment: Assessment, idle_executor: None
) -> None:
    criteria_marking(assessment)
    question = assessment.questions.get(key="Q3")
    page = client.get(reverse("question", args=[assessment.pk, question.pk])).content.decode()
    assert "This marking uses AI" in page


def test_choosing_again_leaves_the_running_choice(client: Client, idle_executor: None) -> None:
    post_model_service(client, "model_service_choose", {"chosen_by": "automatically"})
    post_model_service(client, "model_service_choose", {"chosen_by": "automatically"})
    assert Job.objects.filter(kind=Job.Kind.MODEL_CHOICE, status__in=Job.ACTIVE).count() == 1


def test_a_job_is_lost_past_its_own_time_limit(user: User, idle_executor: None) -> None:
    started = timezone.now() - timedelta(seconds=700)
    short_limit_job = Job.objects.create(
        kind=Job.Kind.MODEL_CHOICE, status=Job.Status.RUNNING, started_at=started, time_limit=600
    )
    long_limit_job = Job.objects.create(
        kind=Job.Kind.MODEL_CHOICE, status=Job.Status.RUNNING, started_at=started, time_limit=1800
    )
    assert list(Job.objects.timed_out()) == [short_limit_job]
    assert list(Job.objects.running()) == [long_limit_job]


def test_choosing_without_a_connected_device_says_so(client: Client, idle_executor: None) -> None:
    response = client.post(reverse("model_service_choose"), {"chosen_by": "myself", "fast_model": "decider"})
    assert "connected to an AI service." in response.content.decode()
    assert STATE_HEADER not in response
