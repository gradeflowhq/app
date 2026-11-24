"""The landing page's content, in examples.json."""

from functools import cache
from pathlib import Path
from typing import Any

from gradeflow_core import AnswerComparison, QuestionResult, QuestionSet
from gradeflow_core.editing import Decision
from pydantic import BaseModel, ConfigDict

EXAMPLES = Path(__file__).with_name("examples.json")


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


class Groups(LandingModel):
    question_key: str
    question_set: QuestionSet
    answers: list[Answer]
    """The commonest first."""
    meaning_comparison: AnswerComparison
    """The meaning model's comparison of the answers, as the app stores it."""
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


class ExampleVariant(LandingModel):
    change: str
    """What changed from the variant before, or how the first starts."""
    marking: MarkingJson
    results: list[QuestionResult] | None = None
    """Its answers' results when a language model marks them, from the core's example of the method."""


class Example(LandingModel):
    """A method marking one question, or a question group's questions together."""

    method_ref: str
    question_set: QuestionSet
    answers: list[ExampleSubmission]
    variants: list[ExampleVariant]


# The hero.


class HeroAnswer(Answer):
    result: QuestionResult
    """What the mark scheme gives it. Given here, as its Key Points need a local model and its Answer Criteria the AI
    service."""


class OtherQuestion(LandingModel):
    question_key: str
    method_ref: str


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
