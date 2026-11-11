import pytest
from django.test import Client
from django.utils.html import escape
from gradeflow_core.editing import Condition

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.marking.workspace import MarkingWorkspace
from tests.conftest import start_marking


@pytest.mark.django_db
@pytest.mark.parametrize("nested", [False, True])
def test_a_check_with_nothing_before_it_has_no_condition(client: Client, assessment: Assessment, nested: bool) -> None:
    method = "gradeflow/workflow@1" if nested else "gradeflow/length@1"
    url, _ = start_marking(client, assessment, "Q1", method)
    path = "/with/steps/0" if nested else ""
    if nested:
        client.post(url, {"path": "", "operation": "add", "target": path, "method": "gradeflow/length@1"})
    page = client.post(url, {"path": "", "operation": "open", "target": path}).text
    assert 'name="if-' not in page


@pytest.mark.django_db
def test_a_condition_on_an_earlier_check_names_that_check(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/workflow@1")
    for index, method in enumerate(("gradeflow/key-points@1", "gradeflow/length@1")):
        client.post(url, {"path": "", "operation": "add", "target": f"/with/steps/{index}", "method": method})
    draft.refresh_from_db()
    earlier_step_label = escape(MarkingWorkspace(draft).marking_draft.label("/with/steps/0"))
    page = client.post(url, {"path": "", "operation": "open", "target": "/with/steps/1"}).text
    assert f'<option value="/with/steps/0">{earlier_step_label}</option>' in page
    tests = [(test, escape(label)) for test, label in Condition.test_labels.items()]
    assert all(f'<option value="{test}"' in page and f">{label}</option>" in page for test, label in tests)
    client.post(
        url, {"path": "/with/steps/1", "fields_posted": "1", "if-check-0": "/with/steps/0", "if-test-0": "incorrect"}
    )
    reloaded_page = client.post(url, {"path": "", "operation": "open", "target": "/with/steps/1"}).text
    assert f'<option value="/with/steps/0" selected>{earlier_step_label}</option>' in reloaded_page
    assert '<option value="incorrect" selected>' in reloaded_page
