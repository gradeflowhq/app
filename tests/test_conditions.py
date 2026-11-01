import pytest
from django.test import Client
from django.utils.html import escape
from gradeflow_core.editing import Condition

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.marking.workspace import Workspace
from tests.conftest import start_marking


@pytest.mark.django_db
@pytest.mark.parametrize("nested", [False, True])
def test_a_check_with_nothing_before_it_has_no_condition(client: Client, assessment: Assessment, nested: bool) -> None:
    method = "gradeflow/combination@1" if nested else "gradeflow/length@1"
    url, _ = start_marking(client, assessment, "Q1", method)
    path = "with.checks.0" if nested else ""
    if nested:
        client.post(url, {"path": "", "op": "add", "target": path, "value": "gradeflow/length@1"})
    page = client.post(url, {"path": "", "op": "open", "target": path}).content.decode()
    assert 'name="if-' not in page


@pytest.mark.django_db
def test_a_condition_on_an_earlier_check_names_that_check(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/combination@1")
    for at, method in enumerate(("gradeflow/key-points@1", "gradeflow/length@1")):
        client.post(url, {"path": "", "op": "add", "target": f"with.checks.{at}", "value": method})
    draft.refresh_from_db()
    earlier = escape(Workspace(draft).checks.label("with.checks.0"))
    page = client.post(url, {"path": "", "op": "open", "target": "with.checks.1"}).content.decode()
    assert f'<option value="with.checks.0">{earlier}</option>' in page
    tests = [(test, escape(label)) for test, label in Condition.tests.items()]
    assert all(f'<option value="{test}"' in page and f">{label}</option>" in page for test, label in tests)
    client.post(url, {"path": "with.checks.1", "fields": "1", "if-check-0": "with.checks.0", "if-test-0": "incorrect"})
    again = client.post(url, {"path": "", "op": "open", "target": "with.checks.1"}).content.decode()
    assert f'<option value="with.checks.0" selected>{earlier}</option>' in again
    assert '<option value="incorrect" selected>' in again
