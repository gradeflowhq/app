import re
from html import unescape

import pytest
from django.test import Client
from django.urls import reverse
from gradeflow_core import BUILTINS
from gradeflow_core.editing import MarkingDraft

from gradeflow_app.landing.by_example import CHAPTERS as BY_EXAMPLE_CHAPTERS
from gradeflow_app.landing.content import landing_content
from gradeflow_app.landing.groups import CHAPTERS as GROUPS_CHAPTERS
from gradeflow_app.landing.hero import CHAPTERS
from gradeflow_app.landing.page import SECTIONS
from gradeflow_app.landing.trust import CLAIMS

pytestmark = pytest.mark.django_db


def test_visitors_see_what_gradeflow_does() -> None:
    response = Client().get(reverse("landing"))
    assert response.status_code == 200
    page = response.text
    for section in SECTIONS:
        assert f'id="{section}"' in page
    assert '<span class="code-k">def</span>' in page


def test_the_hero_shows_an_assessment_from_its_import_to_canvas() -> None:
    page = unescape(Client().get(reverse("landing")).text)
    hero = landing_content().hero
    description = hero.question_set[hero.question_key].description
    assert description
    expected_texts = [
        hero.file_name,
        description,
        *(
            MarkingDraft(stage.marking, hero.question_set, stage.marking.get("question")).label(stage.open_step_path)
            for stage in hero.editing_stages
            if stage.open_step_path
        ),
        hero.adjustment.feedback,
        *(student.student_id for student in hero.students),
        f"Sent {sum(answer.student_count for answer in hero.answers)} grades to “{hero.assessment}”.",
    ]
    for text in [*CHAPTERS, *expected_texts]:
        assert text in page


def test_the_hero_editor_hides_field_help() -> None:
    page = Client().get(reverse("landing")).text
    [editor, *_] = re.findall(r"<div [^>]*data-marking-editor>", page)
    assert "[&_.field-help]:hidden" in editor


def test_each_method_shows_every_change_to_its_settings() -> None:
    page = unescape(Client().get(reverse("landing")).text)
    for key, example in landing_content().examples.items():
        assert f'data-example="{key}"' in page
        for variant in example.variants:
            assert variant.change in page


def test_the_trust_demo_shows_each_claim_on_its_own_answers() -> None:
    page = unescape(Client().get(reverse("landing")).text)
    trust = landing_content().trust
    for text in [
        *CLAIMS,
        *(description for _, description in CLAIMS.values()),
        *(answer.text for answer in [*trust.same_mark.answers, *trust.breakdown.answers]),
        *(row.answers[key] for row in trust.ai.answers for key in row.answers),
        trust.student_names[0],
    ]:
        assert text in page


def test_the_demos_show_answers_grouped_and_marked_by_example() -> None:
    page = unescape(Client().get(reverse("landing")).text)
    content = landing_content()
    groups, by_example = content.groups, content.by_example
    for text in [*GROUPS_CHAPTERS, *(answer.text for answer in groups.answers), groups.adjustment.feedback]:
        assert text in page
    for text in [*BY_EXAMPLE_CHAPTERS, *(answer.text for answer in by_example.answers)]:
        assert text in page


def test_people_signed_in_go_to_their_courses(client: Client) -> None:
    assert client.get(reverse("landing")).headers["Location"] == reverse("courses")


def test_the_language_model_examples_end_as_the_cores_examples_do() -> None:
    for example in landing_content().examples.values():
        if (results := example.variants[-1].results) is None:
            continue
        [core_example] = [
            core_example
            for core_example in BUILTINS.find(example.method_ref).examples
            if core_example.with_ == example.variants[-1].marking["with"]
        ]
        [question] = example.question_set
        expected = {row.answers[question]: row.expected[question] for row in core_example.rows}
        assert {
            answer.answers[question]: (result.marks, result.feedback)
            for answer, result in zip(example.answers, results, strict=True)
        } == {answer: (row.marks, row.feedback) for answer, row in expected.items()}
