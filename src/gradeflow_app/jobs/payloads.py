"""What a sandbox job runs on: everything it marks, captured when it starts. The sandbox reads the same JSON."""

from typing import Annotated, Any, Literal

from gradeflow_core import ModelEndpoint, QuestionSet, Submission
from gradeflow_core.editing import ExampleRow
from pydantic import BaseModel, PlainSerializer


def _with_token(endpoint: ModelEndpoint) -> dict[str, Any]:
    return {**endpoint.model_dump(mode="json"), "token": endpoint.token.get_secret_value()}


Endpoint = Annotated[ModelEndpoint, PlainSerializer(_with_token)]
"""A model endpoint with its token, which the sandbox needs; the job lets its input go when it ends."""


class Payload(BaseModel):
    """Students' answers and the markings that mark them."""

    questions: QuestionSet
    markings: list[dict[str, Any]]
    methods: list[tuple[str, str]]
    """The published versions the markings use, each method's name and YAML."""
    submissions: list[Submission]
    model_endpoint: Endpoint | None = None


class TestRun(BaseModel):
    """Test answers for one method, which may be a draft."""

    method: str
    methods: list[tuple[str, str]]
    questions: QuestionSet
    question: str | None
    inputs: dict[str, Any]
    rows: list[ExampleRow]
    model_endpoint: Endpoint | None = None


class ModelChoiceRun(BaseModel):
    """A model endpoint whose models to choose."""

    model_endpoint: Endpoint


class ModelTry(BaseModel):
    model: str
    role: Literal["decision", "reasoning"]


class ModelTestRun(BaseModel):
    """Models of an endpoint to try, each in a role."""

    model_endpoint: Endpoint
    tries: list[ModelTry]
