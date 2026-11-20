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


class Answer(LandingModel):
    text: str
    student_count: int


class GroupAdjustment(LandingModel):
    """The marks and feedback set for the students in the group of `answer`."""

    answer: str
    marks: float
    feedback: str


# The grouping demo.


class AnswerWithMeaningGroup(Answer):
    meaning_group: str
    """Its group by meaning, at GradeFlow's default of 85% similar."""


class Groups(LandingModel):
    question_key: str
    question_set: QuestionSet
    answers: list[AnswerWithMeaningGroup]
    """The commonest first."""
    marking: MarkingJson
    adjustment: GroupAdjustment


# The marking-by-example demo.


class ByExample(LandingModel):
    question_key: str
    question_set: QuestionSet
    answers: list[Answer]
    """The commonest first."""
    marking: MarkingJson
    """The marking before marking by example."""
    decisions: dict[str, Decision]
    """Each decision, by answer, in the order they're made."""


# The method demos.


class ExampleSubmission(LandingModel):
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
    """A method marking one question, or a question group's questions together."""

    method_ref: str
    question_set: QuestionSet
    answers: list[ExampleSubmission]
    variants: list[ExampleVariant]


# The hero.


class HeroAnswer(Answer):
    result: LanguageModelResult
    """What the mark scheme gives it. Given here, as its Key Points need a local model and its Answer Criteria the AI
    service."""


class OtherQuestion(LandingModel):
    question_key: str
    method_ref: str
    average_percent: int


class EditingStage(LandingModel):
    marking: MarkingJson
    open_step_path: str | None = None


class HeroStudent(LandingModel):
    student_id: str
    answer: str
    """Their answer to the question marked."""
    marks: dict[str, float]
    """Their marks for the other questions, by question."""


class Hero(LandingModel):
    course: str
    assessment: str
    file_name: str
    marker_name: str
    canvas_host: str
    question_key: str
    question_set: QuestionSet
    other_questions: list[OtherQuestion]
    answers: list[HeroAnswer]
    """The commonest first."""
    editing_stages: list[EditingStage]
    """The mark scheme as it's written, one stage for each change the editor shows."""
    adjustment: GroupAdjustment
    students: list[HeroStudent]


# The trust demo.


class TrustQuestion(LandingModel):
    question_key: str
    question_set: QuestionSet
    answers: list[Answer]
    """The commonest first."""
    marking: MarkingJson


class Trust(LandingModel):
    same_mark: TrustQuestion
    student_names: list[str]
    """The student IDs of `same_mark`, in its answers' order: names, as an ID column holding names gives them."""
    breakdown: TrustQuestion
    opened_answer: str
    """The answer to `breakdown` whose group the demo opens."""
    ai: Example
    """Marked by rules, then by a method that asks the AI service. Its results are what a model gave when run once."""


class LandingContent(LandingModel):
    hero: Hero
    trust: Trust
    groups: Groups
    by_example: ByExample
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


class MethodChoice(LandingModel):
    """A method to choose, as the lists of methods show it."""

    ref: str
    label: str
    summary: str
    needs_model_endpoint: bool


class CoreEditingStage(LandingModel):
    """What the editor shows at a stage of writing the mark scheme."""

    marking: EditorContent
    open_step: EditorContent | None


class CoreHero(LandingModel):
    method_labels: dict[str, str]
    """The label of each other question's method, by question."""
    normalized_answers: dict[str, str]
    """Each answer as the question reads it, by answer."""
    results: dict[str, QuestionResult]
    """Each answer's result, by answer."""
    everyday: list[MethodChoice]
    """The everyday methods offered for the question."""
    advanced_choices: list[MethodChoice]
    step_choices: list[MethodChoice]
    """The everyday methods offered for a step of its marking."""
    editing_stages: list[CoreEditingStage]
    summary: MarkingSummary
    """The mark scheme, saved."""


class CoreGroups(LandingModel):
    normalized_answers: dict[str, str]
    """Each answer as the question reads it, by answer."""
    results: dict[str, QuestionResult]
    """Each answer's result, by answer."""
    spelling_groups: list[list[str]]
    """The answers of each group by similar spelling, as the answers column groups them."""


class CoreByExampleStage(LandingModel):
    """The marking after a decision of marking by example, or before the first."""

    results: dict[str, QuestionResult]
    """Each answer's result, by answer."""
    summary: MarkingSummary
    decisions: dict[str, Decision]
    """The decisions so far, by answer as the question reads it."""


class CoreByExample(LandingModel):
    normalized_answers: dict[str, str]
    """Each answer as the question reads it, by answer."""
    stages: list[CoreByExampleStage]


class CoreVariant(LandingModel):
    summary: MarkingSummary
    results: list[dict[str, QuestionResult]]
    """Each answer's result for each question the marking gives marks to, by question."""


class CoreExample(LandingModel):
    method_label: str
    normalized_answers: list[dict[str, str]]
    """Each answer to each question, as the question reads it."""
    variants: list[CoreVariant]


class CoreTrustQuestion(LandingModel):
    normalized_answers: dict[str, str]
    """Each answer as the question reads it, by answer."""
    results: dict[str, QuestionResult]
    """Each answer's result, by answer."""


class CoreTrust(LandingModel):
    same_mark: CoreTrustQuestion
    breakdown: CoreTrustQuestion
    ai: CoreExample


class CoreOutput(LandingModel):
    hero: CoreHero
    trust: CoreTrust
    groups: CoreGroups
    by_example: CoreByExample
    examples: dict[str, CoreExample]
    other_methods: list[str]
    """The labels of the methods for the page's questions that have no example, simplest first."""


@cache
def core_output() -> CoreOutput:
    return CoreOutput.model_validate_json(CORE.read_text())
