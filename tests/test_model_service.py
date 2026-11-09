import json
from dataclasses import asdict
from typing import Any, cast

import pytest
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from gradeflow_core import GradeFlowError, ModelTrial, Progress
from gradeflow_core.editing import dump_yaml
from pytest_django import Settings

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs import runs
from gradeflow_app.jobs.runner import Ending, Run
from gradeflow_app.jobs.runs import ENDING_REASONS, UNAVAILABLE_REASON
from gradeflow_app.libraries.designer.workspace import MethodWorkspace
from gradeflow_app.libraries.models import Method
from gradeflow_app.marking.answers.column import out_of_date_notices
from gradeflow_app.marking.checks import assessment_checks
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.remarks import schedule_marking
from gradeflow_app.model_service import views
from gradeflow_app.model_service.endpoints import SERVICE_HEADER, TOKEN_HEADER, endpoint
from gradeflow_app.model_service.models import ANSWERS_PER_REQUEST_LIMITS, ModelService, ModelTest
from gradeflow_app.requests import Request
from tests.conftest import FakeRunner, create_method_draft, full_marks_marking, post_callback, signature

PROVIDER_URL = "https://models.example.com/v1"
PROVIDER = "Example AI"
MODEL_SERVICE_HEADERS = {SERVICE_HEADER: PROVIDER, TOKEN_HEADER: "secret-token"}
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
CRITERIA = {"criteria": [{"text": "Says ice floats", "marks": 1}]}


@pytest.fixture(autouse=True)
def _offer_provider(settings: Settings) -> None:
    settings.GRADEFLOW_MODEL_PROVIDERS = {PROVIDER: PROVIDER_URL}


def model_service(user: User, *trials: ModelTrial, **fields: Any) -> ModelService:
    """`user`'s use of the provider, with `trials` tested."""
    service = ModelService.objects.create(user=user, url=PROVIDER_URL, **fields)
    for trial in trials:
        fields = trial.model_dump(exclude={"failure"}) | {"failure": trial.failure or ""}
        ModelTest.objects.create(service=service, **fields)
    return service


def request_with_model_service(user: User) -> Request:
    """A request from `user`'s browser, carrying its AI service's headers."""
    request = cast(Request, RequestFactory().post("/", headers=MODEL_SERVICE_HEADERS))
    request.user = user
    return request


def post_model_service(client: Client, name: str, data: dict[str, Any] | None = None) -> HttpResponse:
    """A request connected to the provider, and the runs it starts or stops once it commits."""
    with TestCase.captureOnCommitCallbacks(execute=True):
        return cast(HttpResponse, client.post(reverse(name), data or {}, headers=MODEL_SERVICE_HEADERS))


def run_input(run_id: str) -> Any:
    """What the run fetches to work on."""
    run = Run.parse(run_id)
    assert run is not None
    headers = {"X-GradeFlow-Signature": signature(runs.secret(run), b"input")}
    return Client().get(reverse("job_input", args=[run_id]), headers=headers).json()


def endpoint_sent(fake_runner: FakeRunner, run_id: str) -> Any:
    """The model endpoint the run was given, with its token."""
    env = fake_runner.launches[run_id].environment()
    return json.loads(env["GRADEFLOW_MODEL_ENDPOINT"]) if "GRADEFLOW_MODEL_ENDPOINT" in env else None


def time_out(fake_runner: FakeRunner, run_id: str) -> None:
    """End the run as the runner does one past its time limit."""
    fake_runner.end(run_id, Ending.TIMED_OUT)


def in_use(user: User) -> tuple[str, str, str]:
    service = ModelService.objects.get(user=user)
    return (*service.models_in_use(), service.chosen_by)


def criteria_marking(assessment: Assessment) -> Marking:
    question = assessment.questions.get(key="Q3")
    question.definition = {"type": "text", "max_marks": 1}
    question.save()
    definition = {"question": "Q3", "uses": "gradeflow/answer-criteria@1", "with": CRITERIA}
    return Marking.objects.create(assessment=assessment, question=question, definition=dump_yaml(definition))


def mark_with_criteria(assessment: Assessment, user: User) -> str:
    """The ID of the run marking Q3 with Answer Criteria, from someone connected to the provider."""
    with TestCase.captureOnCommitCallbacks(execute=True):
        run = schedule_marking(criteria_marking(assessment), user, endpoint(request_with_model_service(user)))
    assert run is not None
    return run.id


def test_the_ai_service_is_among_the_accounts_integrations(client: Client) -> None:
    page = client.get(reverse("integrations")).text
    assert "<h1>Integrations</h1>" in page
    assert f"<option>{PROVIDER}</option>" in page
    assert 'id="ai-service-guide"' in page
    assert 'id="model-service"' not in client.get(reverse("settings")).text


def test_a_server_without_ai_services_says_so(client: Client, settings: Settings) -> None:
    settings.GRADEFLOW_MODEL_PROVIDERS = {}
    assert "This server offers no AI service." in client.get(reverse("integrations")).text


def test_connecting_asks_before_choosing_automatically(
    client: Client, user: User, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "list_models", lambda endpoint: ["decider", "reasoner"])
    page = post_model_service(client, "model_service_connect").text
    assert not fake_runner.launches
    assert "Not chosen yet. Choosing automatically tests up to 2 models on sample answers." in page
    assert "Choose automatically" in page
    assert "Pick myself" in page
    assert 'id="model-chooser"' not in page
    assert in_use(user) == ("", "", "")


def test_connecting_again_keeps_the_models_chosen(
    client: Client, user: User, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "list_models", lambda endpoint: ["decider"])
    model_service(user, fast_model="decider", chosen_by="myself")
    page = post_model_service(client, "model_service_connect").text
    assert not fake_runner.launches
    assert '<code class="truncate text-base-content">decider</code>' in page


def test_choosing_automatically_runs_a_job_with_the_token_and_whats_been_tested(
    client: Client, user: User, idle_runner: FakeRunner
) -> None:
    model_service(user, MODEL_TRIALS[1])
    post_model_service(client, "model_service_choose")
    [run_id] = idle_runner.run_ids("model-choice")
    assert endpoint_sent(idle_runner, run_id) == {
        "url": PROVIDER_URL,
        "token": "secret-token",
        "fast_model": None,
        "careful_model": None,
        "answers_per_request": ANSWERS_PER_REQUEST_LIMITS["default"],
    }
    assert run_input(run_id) == {"tried": TRIALS[1:]}
    assert "Testing models…" in post_model_service(client, "model_service").text
    assert in_use(user) == ("tiny", "", "automatically"), "the best so far, though it missed some"


def test_the_models_dialog_lists_every_model_with_how_each_did(
    client: Client, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "list_models", lambda endpoint: ["tiny", "decider", "reasoner"])
    model_service(user, *MODEL_TRIALS, chosen_by="automatically")
    dialog = post_model_service(client, "model_service_dialog").text
    assert (
        dialog.index('data-search="decider"')
        < dialog.index('data-search="tiny"')
        < dialog.index('data-search="reasoner"')
    ), "in use first, then passed, then the rest"
    assert "6/6 · 4 s" in dialog
    assert "0/6 · 2 s" in dialog, "a model that failed shows its time too"
    assert 'title="Missed 6 of 6 example answers."' in dialog
    assert 'value="decider" class="radio radio-xs radio-primary"' in dialog
    assert dialog.count(">Best</span>") == 1
    assert 'hx-trigger="change" checked>' in dialog, "Use the best automatically is on"
    assert dialog.count(">Test</button>") == 4, "each untested model in each role"
    assert '<input type="hidden" name="listed" value="reasoner">' in dialog


def test_picking_a_model_uses_it_from_then_on(client: Client, user: User) -> None:
    model_service(user, *MODEL_TRIALS, chosen_by="automatically")
    response = post_model_service(client, "model_service_use", {"fast_model": "tiny", "listed": "decider"})
    assert in_use(user) == ("tiny", "", "myself")
    assert 'id="model-service-models" class="divide-y divide-base-300" hx-swap-oob="outerMorph"' in response.text
    assert 'id="model-table"' in response.text
    post_model_service(client, "model_service_automatic", {"automatically": "on"})
    assert in_use(user) == ("decider", "", "automatically")
    post_model_service(client, "model_service_automatic")
    assert in_use(user) == ("decider", "", "myself"), "turning it off keeps the models in use"


def test_the_table_keeps_its_order_while_the_dialog_is_open(client: Client, user: User) -> None:
    model_service(user, *MODEL_TRIALS, fast_model="decider", chosen_by="myself")
    picked = {"fast_model": "tiny", "listed": ["decider", "tiny", "reasoner"]}
    table = post_model_service(client, "model_service_use", picked).text
    assert (
        table.index('data-search="decider"') < table.index('data-search="tiny"') < table.index('data-search="reasoner"')
    )


def test_a_change_from_the_card_answers_with_the_card_alone(client: Client, idle_runner: FakeRunner) -> None:
    page = post_model_service(client, "model_service_choose").text
    assert 'id="model-table"' not in page
    assert 'id="model-service-models"' in page


def test_connecting_refuses_a_token_the_service_refuses(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse_token(endpoint: object) -> list[str]:
        raise GradeFlowError("The AI service refused the token.")

    monkeypatch.setattr(views, "list_models", refuse_token)
    response = post_model_service(client, "model_service_connect")
    assert b"The AI service refused the token." in response.content
    assert f"<option selected>{PROVIDER}</option>".encode() in response.content
    assert b'data-refused="true"' in response.content


def test_only_the_servers_ai_services_connect(client: Client, user: User) -> None:
    unoffered = {SERVICE_HEADER: "Elsewhere", TOKEN_HEADER: "secret-token"}
    response = client.post(reverse("model_service_connect"), headers=unoffered)
    assert b"Choose an AI service and enter its token." in response.content
    assert b'data-refused="true"' in response.content
    request = cast(Request, RequestFactory().post("/", headers=unoffered))
    request.user = user
    assert endpoint(request) is None
    model_endpoint = endpoint(request_with_model_service(user))
    assert model_endpoint is not None
    assert model_endpoint.url == PROVIDER_URL


def test_choosing_automatically_uses_the_best_models_so_far(
    client: Client, user: User, idle_runner: FakeRunner
) -> None:
    model_service(user, MODEL_TRIALS[1], fast_model="earlier", chosen_by="myself")
    post_model_service(client, "model_service_choose")
    [run_id] = idle_runner.run_ids("model-choice")
    assert post_callback(run_id, {"event": "trying", "model": "decider", "role": "fast"}) == 204
    page = post_model_service(client, "model_service").text
    assert in_use(user) == ("tiny", "", "automatically")
    assert "<code>decider</code> as fast" in page
    table = post_model_service(client, "model_service_table", {"listed": ["decider", "tiny"]}).text
    assert table.count("Testing…") == 1
    assert post_callback(run_id, {"event": "tried", "trial": TRIALS[0]}) == 204
    page = post_model_service(client, "model_service").text
    assert in_use(user) == ("decider", "", "automatically")
    assert ">decider</code>" in page
    assert "Testing models · 1 done" in page
    assert post_callback(run_id, {"event": "finished"}) == 204
    assert in_use(user) == ("decider", "", "automatically")
    assert "Testing models" not in post_model_service(client, "model_service").text


def test_a_choice_with_nothing_left_to_try_ends_without_a_failure(
    client: Client, user: User, idle_runner: FakeRunner
) -> None:
    post_model_service(client, "model_service_choose")
    [run_id] = idle_runner.run_ids("model-choice")
    assert post_callback(run_id, {"event": "finished"}) == 204
    assert not idle_runner.held
    assert ModelService.objects.get(user=user).choice_failure == ""
    assert "Couldn't choose automatically" not in post_model_service(client, "model_service").text


def test_testing_a_model_shows_how_it_did_as_soon_as_it_finishes(client: Client, idle_runner: FakeRunner) -> None:
    listed: dict[str, Any] = {"listed": ["decider", "reasoner"]}
    post_model_service(client, "model_service_test", listed | {"role": "fast", "model": "decider"})
    post_model_service(client, "model_service_test", listed | {"role": "careful", "model": "reasoner"})
    assert post_model_service(client, "model_service_table", listed).text.count("Testing…") == 2
    job_ids = idle_runner.run_ids("model-trial")
    assert [run_input(run_id)["trials"] for run_id in job_ids] == [
        [{"model": "decider", "role": "fast"}],
        [{"model": "reasoner", "role": "careful"}],
    ]
    assert post_callback(job_ids[0], {"event": "tried", "trial": TRIALS[0]}) == 204
    table = post_model_service(client, "model_service_table", listed).text
    assert "6/6 · 4 s" in table
    assert table.count("Testing…") == 1
    time_out(idle_runner, job_ids[1])
    table = post_model_service(client, "model_service_table", listed).text
    assert "Testing…" not in table, "a test whose job ended without a result can be run again"
    assert f"Couldn't test: {ENDING_REASONS[Ending.TIMED_OUT]}" in table


def test_a_model_the_service_failed_on_can_be_tried_again(
    client: Client, user: User, idle_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    errored = ModelTrial(
        model="decider",
        role="fast",
        rows_matched=0,
        rows_tried=None,
        seconds=0.1,
        failure="The AI service answered 503.",
    )
    model_service(user, errored)
    monkeypatch.setattr(views, "list_models", lambda endpoint: ["decider"])
    dialog = post_model_service(client, "model_service_dialog").text
    assert '<span class="line-clamp-2">The AI service answered 503.</span>' in dialog
    assert 'aria-label="Retry decider as the fast model"' in dialog
    post_model_service(client, "model_service_test", {"role": "fast", "model": "decider", "listed": "decider"})
    [run_id] = idle_runner.run_ids("model-trial")
    assert post_callback(run_id, {"event": "tried", "trial": TRIALS[0]}) == 204
    assert "6/6 · 4 s" in post_model_service(client, "model_service_table", {"listed": "decider"}).text


def test_testing_again_keeps_the_last_result_until_a_new_one(
    client: Client, user: User, idle_runner: FakeRunner
) -> None:
    model_service(user, MODEL_TRIALS[0], chosen_by="automatically")
    listed = {"listed": "decider"}
    post_model_service(client, "model_service_test", listed | {"role": "fast", "model": "decider"})
    assert "Testing…" in post_model_service(client, "model_service_table", listed).text
    assert in_use(user)[0] == "decider", "it's still in use while it's tested again"
    idle_runner.is_down = True
    post_model_service(client, "model_service_test", listed | {"role": "fast", "model": "decider"})
    idle_runner.is_down = False
    table = post_model_service(client, "model_service_table", listed).text
    assert "Testing…" not in table
    assert "6/6 · 4 s" in table
    assert f"Couldn't test: {UNAVAILABLE_REASON}" in table
    assert 'aria-label="Test decider again as the fast model"' in table


def test_a_trial_that_cant_be_read_is_refused(client: Client, idle_runner: FakeRunner) -> None:
    post_model_service(client, "model_service_test", {"role": "fast", "model": "decider"})
    [run_id] = idle_runner.run_ids("model-trial")
    assert post_callback(run_id, {"event": "tried", "trial": {"model": "decider"}}) == 400
    assert not ModelTest.objects.exclude(seconds=None).exists()


def test_an_automatic_choice_that_failed_says_why(client: Client, idle_runner: FakeRunner) -> None:
    post_model_service(client, "model_service_choose")
    [run_id] = idle_runner.run_ids("model-choice")
    post_callback(run_id, {"event": "failed", "error": "No model got every example right."})
    page = post_model_service(client, "model_service").text
    assert "Couldn't choose automatically: No model got every example right." in page


def test_answers_per_request_are_kept_and_sent_with_runs(client: Client, user: User, idle_runner: FakeRunner) -> None:
    post_model_service(client, "model_service_answers_per_request", {"answers_per_request": "6"})
    assert ModelService.objects.get(user=user).answers_per_request == 6
    limits = ANSWERS_PER_REQUEST_LIMITS
    too_many = {"answers_per_request": limits["maximum"] + 1}
    refused = post_model_service(client, "model_service_answers_per_request", too_many)
    assert f"Enter a number from {limits['minimum']} to {limits['maximum']}." in refused.text
    assert f'value="{limits["maximum"] + 1}"' in refused.text, "the box keeps what was typed, beside its error"
    assert ModelService.objects.get(user=user).answers_per_request == 6
    post_model_service(client, "model_service_choose")
    [run_id] = idle_runner.run_ids("model-choice")
    assert endpoint_sent(idle_runner, run_id)["answers_per_request"] == 6


def test_stopping_ends_the_running_choice_without_calling_it_a_failure(
    client: Client, user: User, idle_runner: FakeRunner
) -> None:
    post_model_service(client, "model_service_choose")
    post_model_service(client, "model_service_stop")
    assert not runs.active("model-choice")
    page = post_model_service(client, "model_service").text
    assert "Couldn't choose automatically" not in page
    assert "Not chosen yet." in page


def test_a_choice_that_ends_early_keeps_the_models_it_found(
    client: Client, user: User, idle_runner: FakeRunner
) -> None:
    post_model_service(client, "model_service_choose")
    [run_id] = idle_runner.run_ids("model-choice")
    post_callback(run_id, {"event": "tried", "trial": TRIALS[0]})
    time_out(idle_runner, run_id)
    page = post_model_service(client, "model_service").text
    assert in_use(user)[0] == "decider"
    assert "Couldn't choose automatically" not in page


def test_marking_that_asks_a_model_carries_the_endpoint_with_its_chosen_models(
    user: User, assessment: Assessment, idle_runner: FakeRunner
) -> None:
    model_service(user, fast_model="decider", chosen_by="myself")
    sent = endpoint_sent(idle_runner, mark_with_criteria(assessment, user))
    assert sent["token"] == "secret-token"
    assert sent["fast_model"] == "decider"


def test_a_method_test_run_asks_models_when_its_test_values_do(user: User) -> None:
    method = Method.objects.create(library=user.library, name="supplied")
    definition = dump_yaml(
        {
            "label": "Supplied",
            "summary": "Marks with the checks given.",
            "question_types": ["text"],
            "inputs": {"checks": {"default": [{"uses": "gradeflow/full-marks@1"}]}},
            "checks": [{"uses": "gradeflow/combination@1", "with": {"checks": "{{ inputs.checks }}"}}],
        }
    )
    workspace = MethodWorkspace(method, create_method_draft(method, user, definition))
    assert not workspace.test_run_asks_models()
    workspace.draft.test_values = {"checks": [{"uses": "gradeflow/answer-criteria@1", "with": CRITERIA}]}
    assert workspace.test_run_asks_models()


def test_marking_that_asks_no_model_leaves_the_token_out(
    user: User, assessment: Assessment, idle_runner: FakeRunner
) -> None:
    with TestCase.captureOnCommitCallbacks(execute=True):
        run = schedule_marking(full_marks_marking(assessment, "Q3"), user, endpoint(request_with_model_service(user)))
    assert run is not None
    assert endpoint_sent(idle_runner, run.id) is None


def test_a_change_without_a_model_service_keeps_the_decisions(user: User, assessment: Assessment) -> None:
    marking = criteria_marking(assessment)
    assert schedule_marking(marking, user) is None
    assert out_of_date_notices(assessment_checks(assessment), ["Q3"]) == [
        "Not marked again: the change came from someone without an AI service. The marks shown are from before."
    ]


def test_jobs_that_ask_a_model_get_longer(
    user: User, assessment: Assessment, idle_runner: FakeRunner, settings: Settings
) -> None:
    settings.GRADEFLOW_MODEL_JOB_TIME_LIMIT = 1800
    assert idle_runner.launches[mark_with_criteria(assessment, user)].time_limit == 1800


def test_marking_says_what_it_waits_on(
    client: Client, user: User, assessment: Assessment, idle_runner: FakeRunner
) -> None:
    run_id = mark_with_criteria(assessment, user)
    progress = Progress(completed=12, total=30, step="Deciding criteria")
    assert post_callback(run_id, {"event": "progress", **asdict(progress)}) == 204
    assert out_of_date_notices(assessment_checks(assessment), ["Q3"]) == []
    question = assessment.questions.get(key="Q3")
    column = client.get(reverse("answers", args=[assessment.pk, question.pk])).text
    assert "Deciding criteria: 12 of 30" in column
    assert "from before" not in column


def test_the_answers_column_asks_to_connect_when_marking_asks_a_model(client: Client, assessment: Assessment) -> None:
    criteria_marking(assessment)
    question = assessment.questions.get(key="Q3")
    page = client.get(reverse("question", args=[assessment.pk, question.pk])).text
    assert "This marking uses AI" in page


def test_a_question_groups_answers_column_asks_to_connect_when_marking_asks_a_model(
    client: Client, assessment: Assessment
) -> None:
    criteria_marking(assessment).delete()
    parts = [
        {"question": "Q3", "uses": "gradeflow/answer-criteria@1", "with": CRITERIA},
        {"question": "Q1", "uses": "gradeflow/full-marks@1"},
    ]
    definition = {"uses": "gradeflow/combination@1", "with": {"checks": parts}}
    marking = Marking.objects.create(
        assessment=assessment, definition=dump_yaml(definition), questions_marked=["Q1", "Q3"]
    )
    column = client.get(reverse("question_group_answers", args=[assessment.pk, marking.pk])).text
    assert "This marking uses AI" in column


def test_choosing_again_leaves_the_running_choice(client: Client, idle_runner: FakeRunner) -> None:
    post_model_service(client, "model_service_choose")
    post_model_service(client, "model_service_choose")
    assert len(idle_runner.run_ids("model-choice")) == 1
    assert len(runs.active("model-choice")) == 1


def test_choosing_without_a_connected_service_says_so(client: Client) -> None:
    response = client.post(reverse("model_service_choose"))
    assert "connected to an AI service." in response.text
