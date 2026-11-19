import json
import re

import pytest
from django.test import Client
from django.urls import reverse

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.results.marks import MIN_STUDENTS
from tests.conftest import create_assessment, encrypted_id, upload

STUDENTS = 30


def marked_assessment(client: Client, columns: dict[str, list[tuple[str, int]]]) -> Assessment:
    """An assessment of `STUDENTS` students, with each question's answers and imported marks, by student."""
    keys = list(columns)
    header = ",".join(f"{key},{key} Pts" for key in keys)
    rows = [
        ",".join(f'"{answer}",{marks}' for answer, marks in (columns[key][i] for key in keys)) for i in range(STUDENTS)
    ]
    csv = f"StudentID,{header}\n" + "".join(f"{encrypted_id(f'S{i:03}')},{row}\n" for i, row in enumerate(rows))
    assessment = create_assessment(client)
    upload(
        client,
        assessment,
        csv,
        answer_columns=json.dumps(keys),
        marks_columns=json.dumps({key: f"{key} Pts" for key in keys}),
    )
    return assessment


def findings(page: str) -> list[str]:
    """Worth a look's rows, as text."""
    panel = page.split("Worth a look</h2>", 1)[1].split("</section>", 1)[0]
    rows = re.findall(r"<a [^>]*href=\"/assessments/[^\"]+\">(.*?)</a>", panel, re.DOTALL)
    return [" ".join(re.sub(r"<[^>]+>", " ", row).split()) for row in rows]


def test_worth_a_look_says_which_questions_weaker_students_did_better_on(client: Client) -> None:
    stronger = [("a", 1) if i >= 10 else ("b", 0) for i in range(STUDENTS)]
    reversed_ = [("x", 1) if i < 10 else ("9.81", 0) for i in range(STUDENTS)]
    assessment = marked_assessment(client, {"Q1": stronger, "Q2": stronger, "Q3": reversed_})
    page = client.get(reverse("overview", args=[assessment.pk])).text
    assert findings(page) == [
        "Q3 Weaker students did better 20 answered “9.81”, marked wrong",
        "Q1 Common wrong answer 10 answered “b”",
        "Q2 Common wrong answer 10 answered “b”",
    ]
    assert "Bottom vs top third" in page
    assert "100% → 0%" in page
    assert "Reliability" in page
    assert ">Unacceptable</span>" in page


def test_worth_a_look_says_which_questions_everyone_or_no_one_got_and_where_blanks_rise(client: Client) -> None:
    def blank_for(count: int) -> list[tuple[str, int]]:
        return [("", 0) if i < count else ("c", 1) for i in range(STUDENTS)]

    right, wrong = [("a", 1)] * STUDENTS, [("b", 0)] * STUDENTS
    assessment = marked_assessment(client, {"Q1": right, "Q2": wrong, "Q3": blank_for(5), "Q4": blank_for(8)})
    page = client.get(reverse("overview", args=[assessment.pk])).text
    assert findings(page) == [
        "Q1 Everyone got full marks 30 of 30",
        "Q2 No one got marks 30 answered “b”",
        "Q3\N{EN DASH}Q4 More blanks near the end 5 and 8 left blank",
    ]


def test_too_few_students_for_worth_a_look_or_the_thirds(client: Client, assessment: Assessment) -> None:
    assert assessment.students.count() < MIN_STUDENTS
    page = client.get(reverse("overview", args=[assessment.pk])).text
    assert "Worth a look" not in page
    assert "Bottom vs top third" not in page
    assert "Reliability" not in page
    assert 'class="marks-bar"' in page


@pytest.mark.parametrize(("wrong_count", "is_common"), [(6, True), (5, False)])
def test_a_wrong_answer_a_fifth_gave_is_worth_a_look(client: Client, wrong_count: int, is_common: bool) -> None:
    def answers(key: str) -> list[tuple[str, int]]:
        return [(f"{key}{i}", 0) if i < 10 else ("a", 1) for i in range(STUDENTS)]

    common = [("b", 0) if i < wrong_count else ("a", 1) if i >= 10 else (f"w{i}", 0) for i in range(STUDENTS)]
    assessment = marked_assessment(client, {"Q1": answers("x"), "Q2": answers("y"), "Q3": common})
    page = client.get(reverse("overview", args=[assessment.pk])).text
    assert findings(page) == ([f"Q3 Common wrong answer {wrong_count} answered “b”"] if is_common else [])
    assert ("Nothing stands out." in page) is not is_common


@pytest.mark.parametrize(("full_count", "is_almost_everyone"), [(29, True), (28, False)])
def test_full_marks_from_95_percent_are_worth_a_look(client: Client, full_count: int, is_almost_everyone: bool) -> None:
    def answers(key: str) -> list[tuple[str, int]]:
        return [(f"{key}{i}", 0) if i < 10 else ("a", 1) for i in range(STUDENTS)]

    mostly_full = [(f"w{i}", 0) if i < STUDENTS - full_count else ("a", 1) for i in range(STUDENTS)]
    assessment = marked_assessment(client, {"Q1": answers("x"), "Q2": answers("y"), "Q3": mostly_full})
    page = client.get(reverse("overview", args=[assessment.pk])).text
    expected = [f"Q3 Almost everyone got full marks {full_count} of {STUDENTS}"]
    assert findings(page) == (expected if is_almost_everyone else [])


def test_activity_opens_from_every_tab(client: Client, assessment: Assessment) -> None:
    activity = f'hx-get="{reverse("activity", args=[assessment.pk])}"'
    for page in ("overview", "questions", "students", "export", "assessment_settings"):
        assert activity in client.get(reverse(page, args=[assessment.pk]), follow=True).text, page
