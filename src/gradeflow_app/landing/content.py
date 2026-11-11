"""The landing page's content, in examples.json, and what the core gives for it, in core.json."""

from functools import cache
from pathlib import Path
from typing import Any

from gradeflow_core import QuestionResult, QuestionSet
from gradeflow_core.editing import Decision, StepsSetting
from pydantic import BaseModel, ConfigDict

from gradeflow_app.editor.presenters import StepCard
from gradeflow_app.marking.presenters import MarkingSummary

EXAMPLES = Path(__file__).with_name("examples.json")
CORE = Path(__file__).with_name("core.json")
"""What the core gives for examples.json. tests/test_landing.py checks it, and writes it when run."""


type MarkingJson = dict[str, Any]
"""A marking's JSON as the core takes it, with ``question`` naming the question it marks unless it is a question
group's."""


class LandingModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# The hero: one question's answers in the workspace, grouped by their feedback.


class Answer(LandingModel):
    text: str
    student_count: int


class WorkspaceQuestion(LandingModel):
    question_key: str
    method: str | None = None
    """The method of its own marking."""
    in_question_group: bool = False
    average: int | None = None
    """Its average mark, as a percentage."""


class WorkspaceQuestionGroup(LandingModel):
    questions: list[str]
    method: str


class Workspace(LandingModel):
    course: str
    assessment: str
    question_key: str
    question_set: QuestionSet
    questions: list[WorkspaceQuestion]
    question_groups: list[WorkspaceQuestionGroup]
    marking: MarkingJson
    edited_marking: MarkingJson
    """The marking without its Docstring step."""
    answer_groups: list[list[Answer]]
    """Answers with the same feedback, the commonest first."""


# The review section: Q1's answers grouped, marked by example and adjusted.


class ReviewAnswer(Answer):
    meaning_group: str
    """Its group by meaning, at GradeFlow's default of 85% similar."""


class ReviewDecision(LandingModel):
    answers: list[str]
    decision: Decision


class ReviewAdjustment(LandingModel):
    answer: str
    marks: float
    feedback: str


class Review(LandingModel):
    question_key: str
    question_set: QuestionSet
    answers: list[ReviewAnswer]
    """The commonest first."""
    marking: MarkingJson
    """The marking before marking by example."""
    decisions: list[ReviewDecision]
    decided_answer: str
    """The answer marked by example that shows open."""
    adjustment: ReviewAdjustment


# The methods: an example of each, its settings changed one step at a time.


class ExampleAnswer(LandingModel):
    answers: dict[str, str]
    """Each answer, by question."""
    student_count: int


class LanguageModelResult(LandingModel):
    marks: float | None
    """None if unmarked."""
    feedback: str


class ExampleVariant(LandingModel):
    change: str
    """What changed from the variant before, or how the first starts."""
    marking: MarkingJson
    results: list[LanguageModelResult] | None = None
    """Its answers' results when a language model marks them, from the core's example of the method."""


class Example(LandingModel):
    method: str
    question_key: str
    question_text: str
    marked_question: str
    """The question whose marks it shows."""
    question_set: QuestionSet
    answers: list[ExampleAnswer]
    variants: list[ExampleVariant]

    @property
    def max_marks(self) -> float:
        return self.question_set[self.marked_question].max_marks


class LandingContent(LandingModel):
    workspace: Workspace
    review: Review
    examples: dict[str, Example]


@cache
def landing_content() -> LandingContent:
    return LandingContent.model_validate_json(EXAMPLES.read_text())


# What the core gives for the content.


class StepCards(LandingModel):
    setting: StepsSetting
    cards: list[StepCard]


class EditorContent(LandingModel):
    """What the editor shows of a marking."""

    label: str
    description: str
    form_schema: dict[str, Any]
    values: dict[str, Any]
    steps: list[StepCards]


class CoreWorkspace(LandingModel):
    method_labels: dict[str, str]
    """The label of each method the questions and question groups use, by reference."""
    normalized: list[list[str]]
    """Each answer of each answer group, as the question reads it."""
    results: list[list[QuestionResult]]
    summary: MarkingSummary
    editor: EditorContent
    edited_results: list[QuestionResult]
    """Each answer's result with the edited marking, in the order of the answer groups."""
    edited_summary: MarkingSummary
    edited_editor: EditorContent


class CoreReview(LandingModel):
    normalized: list[str]
    """Each answer, as the question reads it."""
    results: list[QuestionResult]
    summary: MarkingSummary
    editor: EditorContent
    decided_results: list[QuestionResult]
    """Each answer's result once marked by example."""
    decided_summary: MarkingSummary
    decided_editor: EditorContent
    decisions: dict[str, Decision]
    """The decisions marking by example gave, by answer as the question reads it."""


class CoreVariant(LandingModel):
    description: str
    results: list[QuestionResult]
    """Each answer's result for the question the example shows."""


class CoreExample(LandingModel):
    method_label: str
    method_summary: str
    variants: list[CoreVariant]


class CoreOutput(LandingModel):
    workspace: CoreWorkspace
    review: CoreReview
    examples: dict[str, CoreExample]
    other_methods: list[str]
    """The labels of the methods for the page's questions that have no example, simplest first."""


@cache
def core_output() -> CoreOutput:
    return CoreOutput.model_validate_json(CORE.read_text())
