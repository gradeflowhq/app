from gradeflow_core import QuestionSet, Submission
from gradeflow_core.editing import dump_yaml

from gradeflow_app.jobs.payloads import MarkingRun
from tests.conftest import marked_by_sandbox

QUESTIONS = QuestionSet.model_validate({"Q1": {"type": "text"}, "Q2": {"type": "text"}})


def test_payload_keeps_every_encrypted_id_and_complete_answers() -> None:
    submissions = [
        Submission(student_id="S1", answers={"Q1": "light", "Q2": "a"}),
        Submission(student_id="S2", answers={"Q1": "light", "Q2": "b"}),
    ]
    payload = MarkingRun(
        questions=QUESTIONS,
        markings=[{"question": "Q1", "uses": "gradeflow/full-marks@1"}],
        methods=[],
        submissions=submissions,
    )
    restored = MarkingRun.model_validate(payload.model_dump(mode="json"))
    assert restored.submissions == submissions
    assert [submission.sheet.student_id for submission in marked_by_sandbox(restored)] == ["S1", "S2"]


def test_payload_marks_with_the_published_versions_it_carries() -> None:
    definition = {
        "label": "Light",
        "version": 1,
        "summary": "Says light.",
        "question_types": ["text"],
        "steps": [{"uses": "gradeflow/accepted-answers@1", "with": {"answers": ["light"]}}],
    }
    payload = MarkingRun(
        questions=QUESTIONS,
        markings=[{"question": "Q1", "uses": "lab/light@1"}],
        methods=[("lab/light", dump_yaml(definition))],
        submissions=[Submission(student_id="S1", answers={"Q1": "light"})],
    )
    restored = MarkingRun.model_validate(payload.model_dump(mode="json"))
    assert restored.methods == payload.methods
    assert [list(submission.results) for submission in marked_by_sandbox(restored)] == [["Q1"]]
