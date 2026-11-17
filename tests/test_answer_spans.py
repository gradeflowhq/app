from django.db.models import F
from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse
from gradeflow_core import QuestionResult, Span, ValuesQuestion
from gradeflow_core.editing import dump_yaml

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.marking.answers.answer_groups import AnswerGroup, StudentAnswer
from gradeflow_app.marking.answers.column import preview_marks
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.remarks import schedule_marking
from gradeflow_app.ui.templatetags.ui import highlight_spans

WORD_AFTER_USES_IS_LIGHT = {
    "question": "Q1",
    "uses": "gradeflow/workflow@1",
    "with": {
        "steps": [
            {"id": "word", "uses": "gradeflow/extract@1", "with": {"type": "text", "pattern": r"uses (\w+)"}},
            {
                "input": "{{ steps.word.output }}",
                "uses": "gradeflow/accepted-answers@1",
                "with": {"answers": ["light"]},
            },
        ]
    },
}


def question_page(client: Client, assessment: Assessment, user: User) -> str:
    question = assessment.questions.get(key="Q1")
    marking = Marking.objects.create(
        assessment=assessment, question=question, definition=dump_yaml(WORD_AFTER_USES_IS_LIGHT)
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        schedule_marking(marking, user)
    return client.get(reverse("question", args=[assessment.pk, question.pk])).text


def test_an_answer_group_highlights_the_extracted_text(client: Client, assessment: Assessment, user: User) -> None:
    page = question_page(client, assessment, user)
    assert "photosynthesis uses <mark>light</mark>" in page
    assert page.count("<mark>") == 2  # also in the group "photosynthesis uses light."


def test_a_previous_result_highlights_nothing(client: Client, assessment: Assessment, user: User) -> None:
    question_page(client, assessment, user)
    Marking.objects.update(revision=F("revision") + 1)
    page = client.get(reverse("question", args=[assessment.pk, assessment.questions.get(key="Q1").pk])).text
    assert "<mark>" not in page


def student_answer(normalized: str, spans: tuple[Span, ...]) -> StudentAnswer:
    return StudentAnswer(
        max_marks=1,
        preview_result=QuestionResult(proportion=1, max_marks=1, spans=spans),
        student_pk="",
        encrypted_student_id="",
        answer=normalized,
        normalized=normalized,
    )


def test_a_group_has_spans_only_when_its_students_answers_and_spans_are_the_same() -> None:
    light = student_answer("uses light", (Span(5, 10),))
    assert AnswerGroup([light, light]).spans == (Span(5, 10),)
    assert AnswerGroup([light, student_answer("uses light", ())]).spans == ()
    assert AnswerGroup([light, student_answer("use light", (Span(5, 10),))]).spans == ()


def test_a_previewed_group_has_the_drafts_spans() -> None:
    group = AnswerGroup([student_answer("uses light", (Span(5, 10),))])
    group.preview = preview_marks(group, {"": QuestionResult(proportion=1, max_marks=1, spans=(Span(0, 4),))})
    assert group.spans == (Span(0, 4),)


def test_highlighted_text_is_escaped_inside_and_outside_its_marks() -> None:
    assert highlight_spans("a<b> & c", [Span(1, 4)]) == "a<mark>&lt;b&gt;</mark> &amp; c"
    assert highlight_spans("x < 3", []) == "x &lt; 3"


def test_a_span_in_a_value_is_highlighted_in_that_values_chip() -> None:
    question = ValuesQuestion(value_types=["number", "text"])
    html = render_to_string(
        "results/answer_text.html",
        {"answer": "3, about 42 kg", "core_question": question, "spans": (Span(6, 8, value_number=2),)},
    )
    assert ">3<" in html
    assert "about <mark>42</mark> kg" in html
