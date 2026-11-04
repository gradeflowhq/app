"""Run inputs, built when the run asks. The sandbox reads the same JSON; a model endpoint reaches it through its
environment."""

from typing import Any, Literal

from gradeflow_core import ModelTrial, QuestionSet, Submission
from gradeflow_core.editing import ExampleRow
from pydantic import BaseModel


class MarkingRun(BaseModel):
    """Students' answers and the markings that mark them."""

    questions: QuestionSet
    markings: list[dict[str, Any]]
    methods: list[tuple[str, str]]
    """The published versions the markings use, each method's name and YAML."""
    submissions: list[Submission]


class TestRun(BaseModel):
    """Test answers for one method, which may be a draft."""

    method: str
    methods: list[tuple[str, str]]
    questions: QuestionSet
    question: str | None
    inputs: dict[str, Any]
    rows: list[ExampleRow]


class ComparisonRun(BaseModel):
    """Answers to compare by meaning."""

    texts: list[str]


class ModelChoiceRun(BaseModel):
    """The models tried before at the run's model endpoint, which aren't tried again."""

    tried: list[ModelTrial]


class ModelTrialRequest(BaseModel):
    model: str
    role: Literal["fast", "careful"]


class ModelTrialRun(BaseModel):
    """Models of the run's model endpoint to try, each in a role."""

    trials: list[ModelTrialRequest]
