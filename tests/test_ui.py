import html
import json
import re
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django import forms
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from gradeflow_core.editing import new_question

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.ui.forms import MarkdownInput
from gradeflow_app.ui.templatetags.ui import (
    ago,
    browser,
    code_language,
    highlight,
    history_day,
    initials,
    marks,
    math_markdown,
    percent,
    short_value,
)
from tests.conftest import HTMX_HEADERS, create_assessment

CHROME_ON_LINUX = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


def test_browsers_are_named_in_words() -> None:
    assert browser(CHROME_ON_LINUX) == "Chrome on Linux"
    assert browser("") == "Unknown browser"


def test_times_ago_name_only_their_largest_unit() -> None:
    now = timezone.now()
    assert ago(now - timedelta(seconds=30)) == "just now"
    assert ago(now - timedelta(minutes=61)) == "1\xa0hour ago"
    assert ago(now - timedelta(days=2, hours=15)) == "2\xa0days ago"


def test_days_in_a_history_give_the_year_only_when_it_is_another() -> None:
    now = timezone.now()
    assert history_day(now) == "Today"
    assert history_day(now - timedelta(days=1)) == "Yesterday"
    long_ago = timezone.localtime(now - timedelta(days=400))
    assert history_day(long_ago) == f"{long_ago.day} {long_ago:%B %Y}"


def test_question_text_leaves_formulas_to_the_browser() -> None:
    rendered = math_markdown("Is $\\frac{1}{2} < x$ worth $5 or $6?\n<b>Yes</b>")
    assert '<span class="math inline">\\frac{1}{2} &lt; x</span>' in rendered
    assert "worth $5 or $6?<br />\n&lt;b&gt;Yes&lt;/b&gt;" in rendered


def test_markdown_previews_as_its_pages_show_it(client: Client) -> None:
    text = "Is $x^2$ **big**?\n<b>No</b>"
    preview = client.post(reverse("markdown_preview"), {"text": text}, headers=HTMX_HEADERS).content.decode()
    assert 'x-data="math"' in preview
    assert str(math_markdown(text)) in preview
    assert (
        "Nothing to preview"
        in client.post(reverse("markdown_preview"), {"text": " \n"}, headers=HTMX_HEADERS).content.decode()
    )


def test_read_only_markdown_shows_as_its_pages_show_it() -> None:
    rendered = MarkdownInput().render("guide", "**Bold** $x$", {"disabled": True, "id": "id_guide"})
    assert "<textarea" not in rendered
    assert str(math_markdown("**Bold** $x$")) in rendered


def test_initials_come_from_the_first_two_words() -> None:
    assert [initials(text) for text in ("Introductory Biology", "Final Exam", "Ana", "qa@example.edu")] == [
        "IB",
        "FE",
        "A",
        "QE",
    ]


def test_pages_take_their_width_and_button_sizes_from_the_design_system() -> None:
    templates = Path(__file__).parents[1] / "src" / "gradeflow_app" / "templates"
    for template in templates.rglob("*.html"):
        text = template.read_text()
        assert not re.search(r"<c-page\b[^>]*\swidth=", text), template
        assert "btn-xs" not in text, template
        assert "naturaltime" not in text, template


def test_a_settings_card_posts_its_form_from_its_footer(client: Client) -> None:
    page = client.get(reverse("settings")).content.decode()
    form = re.search(r'<form id="profile" method="post" class="grid gap-5">(.*?)</form>', page, re.DOTALL)
    assert form is not None
    assert 'name="csrfmiddlewaretoken"' in form[1]
    assert re.search(
        r'<footer[^>]*><button form="profile" class="btn btn-primary ml-auto">Save</button></footer>', page
    )


def test_an_alert_shown_by_alpine_is_hidden_until_shown(client: Client) -> None:
    page = client.get(reverse("import", args=[create_assessment(client).pk])).content.decode()
    assert re.search(r'<div role="alert" class="alert alert-error alert-soft *" x-show="error" x-cloak>', page)


def test_a_list_of_settings_shows_each_by_its_first_text() -> None:
    rows = [{"marks": 1, "text": "Wrong idea"}, {"text": "", "point": "Mentions sun"}, {"marks": 2}]
    assert short_value(rows) == "Wrong idea, Mentions sun, 1 setting"


def test_a_table_of_settings_shows_its_row_names() -> None:
    parameters = {"alpha": {"type": "integer", "value": 1}, "beta": {"type": "text", "value": "two"}}
    assert short_value(parameters) == "alpha, beta"


def test_marks_and_percentages_round_halves_up_alike() -> None:
    assert (marks(19 / 40), percent(19 / 40)) == ("0.48", "48%")
    assert (marks(2), marks(1.5), marks(1 / 3), percent(0.125)) == ("2", "1.5", "0.33", "13%")
    assert marks(2.2, 2) == "2.20"


def test_fields_with_errors_are_highlighted() -> None:
    class Sample(forms.Form):
        name = forms.CharField()
        kind = forms.ChoiceField(choices=[("a", "A")])
        notes = forms.CharField(widget=forms.Textarea(attrs={"class": "font-mono"}))
        agreed = forms.BooleanField()

    invalid, valid = Sample({"kind": "b"}), Sample({"name": "Ana", "kind": "a", "notes": "Hi", "agreed": "on"})
    assert 'class="input w-full input-error"' in str(invalid["name"])
    assert 'class="select w-full select-error"' in str(invalid["kind"])
    assert 'class="textarea w-full font-mono textarea-error"' in str(invalid["notes"])
    assert "-error" not in str(invalid["agreed"])
    assert "-error" not in "".join(str(field) for field in valid)


def test_times_show_in_the_browsers_time_zone(client: Client, assessment: Assessment) -> None:
    uploaded_at = assessment.results_files.get().uploaded_at
    client.cookies["tz"] = "Asia/Singapore"
    page = client.get(reverse("activity", args=[assessment.pk]), headers=HTMX_HEADERS).content.decode()
    assert uploaded_at.astimezone(ZoneInfo("Asia/Singapore")).strftime("%H:%M") in page
    client.cookies["tz"] = "Not/A_Zone"
    assert client.get(reverse("activity", args=[assessment.pk]), headers=HTMX_HEADERS).status_code == 200


def test_pages_are_compressed_but_live_streams_are_not(client: Client, assessment: Assessment) -> None:
    url = reverse("questions", args=[assessment.pk])
    stream_url_match = re.search(r'hx-sse:connect="([^"]+)"', client.get(url, follow=True).content.decode())
    assert stream_url_match is not None
    gzip_headers = {"Accept-Encoding": "gzip"}
    assert client.get(url, follow=True, headers=gzip_headers).headers["Content-Encoding"] == "gzip"
    stream = client.get(stream_url_match[1], headers=gzip_headers)
    assert "Content-Encoding" not in stream.headers
    stream.close()


def test_messages_from_in_page_actions_show_at_once(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    student = assessment.students.values_list("pk", flat=True).first()
    response = client.post(
        reverse("adjust", args=[assessment.pk, question.pk]),
        {"student": [str(student)], "marks": "9"},
        headers=HTMX_HEADERS,
    )
    assert response.status_code == 422
    assert {"level": "error", "text": "Marks must be between 0 and 1."} in json.loads(response.headers["X-Messages"])
    assert b"Marks must be between" not in client.get(reverse("overview", args=[assessment.pk])).content


def test_messages_show_once_when_a_whole_page_follows(client: Client) -> None:
    assessment = create_assessment(client)
    client.post(reverse("assessment_settings", args=[assessment.pk]), {"name": "Final", "description": ""})
    page = client.get(reverse("assessment_settings", args=[assessment.pk]), headers=HTMX_HEADERS)
    assert b"Saved." in page.content
    assert "X-Messages" not in page.headers


def test_a_live_refresh_leaves_messages_for_the_page_the_person_goes_to(client: Client) -> None:
    assessment = create_assessment(client)
    client.post(reverse("assessment_settings", args=[assessment.pk]), {"name": "Final", "description": ""})
    live_headers = HTMX_HEADERS | {"X-Live-Refresh": "true"}
    refresh = client.get(reverse("question_list", args=[assessment.pk]), headers=live_headers)
    assert refresh.status_code == 200
    assert "X-Messages" not in refresh.headers
    assert b"Saved." in client.get(reverse("assessment_settings", args=[assessment.pk])).content


def test_messages_survive_a_reload(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    url = reverse("question_settings", args=[assessment.pk, question.pk])
    response = client.post(
        url,
        {"question_type": question.core_question.type, "save": "1", "max_marks": "2", "description": "Changed"},
        headers=HTMX_HEADERS,
    )
    assert response.headers["HX-Refresh"] == "true"
    assert "X-Messages" not in response.headers
    page = client.get(reverse("questions", args=[assessment.pk]), follow=True)
    assert [str(message) for message in page.context["messages"]][-1:] == ["Saved. Marking Q2 again…"]


def test_code_answers_are_told_apart_by_their_question() -> None:
    code, text = new_question("code"), new_question("text")
    assert code_language(code) == "python"
    assert code_language(text) is None
    assert code_language(None) is None
    assert code_language({"Q1": code, "Q2": text}, "Q1") == "python"


def test_code_is_coloured_by_its_language_and_other_text_only_escaped() -> None:
    code = "\ndef f(x):\n    return len(x) < 1  # small\n"
    coloured = highlight(code, "python")
    assert '<span class="code-k">def</span>' in coloured
    assert '<span class="code-nb">len</span>' in coloured
    assert '<span class="code-c1"># small</span>' in coloured
    assert html.unescape(re.sub(r"<[^>]+>", "", coloured)) == code
    assert highlight("<b>", None) == "&lt;b&gt;"
    assert highlight("<b>", "no-such-language") == "&lt;b&gt;"
