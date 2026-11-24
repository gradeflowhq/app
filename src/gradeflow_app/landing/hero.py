"""The hero: an assessment from its import to Canvas, which components/landing/hero.js plays."""

from dataclasses import dataclass
from itertools import pairwise
from statistics import fmean
from typing import Any, Literal, cast

from django import forms
from django.utils import timezone
from gradeflow_core import BUILTINS, Method, Methods, QuestionSet
from gradeflow_core.editing import MarkingDraft, Position, compatible_methods

from gradeflow_app.accounts.models import User
from gradeflow_app.editor.editing import EditorActions, StepWorkspace
from gradeflow_app.editor.guide import choices
from gradeflow_app.editor.picker import PickerForm
from gradeflow_app.editor.presenters import Editor, editor
from gradeflow_app.exports.models import MarksMode
from gradeflow_app.exports.views import Sending
from gradeflow_app.jobs.runs import RunState
from gradeflow_app.landing.content import EditingStage, Hero, HeroAnswer, landing_content
from gradeflow_app.landing.demos import (
    ASSESSMENT_ID,
    AnswersColumn,
    language_model_result,
    largest_first,
    student_answers,
    unsaved_question,
)
from gradeflow_app.libraries.collection import OfferedMethods
from gradeflow_app.marking.answers.answer_groups import AnswerGroup, StudentAnswer
from gradeflow_app.marking.presenters import ListedQuestion, MarkingSummary, Save, marking_badge, summary_of
from gradeflow_app.questions.models import Question
from gradeflow_app.results.marks import Level, level_of
from gradeflow_app.results.models import Adjustment
from gradeflow_app.ui.schemaforms.fields import ListField
from gradeflow_app.ui.schemaforms.forms import ObjectForm

CHAPTERS = {
    "Import": "arrow-up-tray",
    "Mark scheme": "pencil-square",
    "Marking": "check-circle",
    "Adjust marks": "pencil",
    "Students": "users",
    "Send to Canvas": "paper-airplane",
}
"""The chapters the hero plays, with their icons."""
SHOWN_GROUP_COUNT = 6
"""How many answer groups the mockup has room for."""
STARTING_RUN = RunState("running", progress=0.01)
"""A marking run as it starts: the animation fills its progress."""
NO_SAVED_METHODS = OfferedMethods((), (), {})
"""The visitor has no saved methods to choose from."""


class StaticTagInput(forms.SelectMultiple):
    """A list's values as Tom Select shows them, without Tom Select: the hero types into it."""

    template_name = "landing/hero/tags.html"


@dataclass
class HeroWorkspace(StepWorkspace):
    """The editor at one stage of the hero's mark scheme, with no actions and only the fields the hero changes."""

    marking_draft: MarkingDraft
    id_prefix: str
    shown_fields: set[str]

    @property
    def draft(self) -> MarkingDraft:
        return self.marking_draft

    @property
    def offers(self) -> OfferedMethods:
        return NO_SAVED_METHODS

    @property
    def methods(self) -> Methods:
        return BUILTINS

    @property
    def actions(self) -> EditorActions:
        return EditorActions(update="", methods="")

    def form(self, path: str, **kwargs: Any) -> ObjectForm:
        kwargs.setdefault("auto_id", f"{self.id_prefix}-%s")
        if not path:
            return super().form(path, **kwargs)
        schema = self.draft.schema(path)
        properties = {name: field for name, field in schema["properties"].items() if name in self.shown_fields}
        form = ObjectForm({**schema, "properties": properties}, self.draft.values(path), path=path, **kwargs)
        for field in form.fields.values():
            if isinstance(field, ListField):
                field.widget = StaticTagInput()
        return form


type Action = Literal["add_tag", "select", "type", "add_row", "add_step", "close_step"]


@dataclass(frozen=True)
class Edit:
    """What leads from one stage of the mark scheme to the next, as the animation plays it."""

    action: Action
    field_name: str = ""
    value: str = ""
    """What's typed or chosen; for a step added, its method's label."""
    choices: tuple[tuple[str, str], ...] = ()
    """The options of the list it chooses from, which the animation opens."""


@dataclass(frozen=True)
class EditorStageView:
    editor: Editor
    edit: Edit | None
    """What leads to the next stage; None for the last."""


@dataclass(frozen=True)
class MarksCell:
    marks: float
    level: Level
    adjusted: bool


@dataclass(frozen=True)
class StudentRow:
    student_id: str
    total: float
    cells: list[MarksCell]

    @property
    def adjusted(self) -> bool:
        return any(cell.adjusted for cell in self.cells)


@dataclass(frozen=True)
class HeroView:
    content: Hero
    question: Question
    student_count: int
    total_max_marks: float
    question_lists: list[list[ListedQuestion]]
    """Not marked, Q1 being marked, everything marked, and Q1 adjusted."""
    header_columns: list[AnswersColumn]
    """The answers column's head, then while marking."""
    answer_group_lists: list[list[AnswerGroup]]
    """Not marked, marked, and one group adjusted."""
    adjusted_group_index: int
    everyday: list[Method]
    advanced_choices: list[Method]
    step_choices: list[Method]
    picker_title: str
    editor_stages: list[EditorStageView]
    summary: MarkingSummary
    students: list[StudentRow]
    sending_summary: str
    """How the marks go to Canvas."""

    chapters = CHAPTERS

    @property
    def adjusted_count(self) -> int:
        return self.answer_group_lists[-1][self.adjusted_group_index].adjusted_count

    @property
    def hidden_count(self) -> int:
        """How many answer groups the mockup has no room for."""
        return len(self.content.answers) - SHOWN_GROUP_COUNT

    @property
    def marker(self) -> User:
        return User(name=self.content.marker_name)

    @property
    def last_save(self) -> Save:
        """The marker saving the marking just now."""
        return Save(self.marker, timezone.now())


def edit_between(
    before: EditingStage, after: EditingStage, before_draft: MarkingDraft, after_draft: MarkingDraft
) -> Edit:
    """The one change from the stage `before` to the stage `after`, each with its draft."""
    if before.open_step_path != after.open_step_path:
        return (
            Edit("add_step", value=after_draft.label(after.open_step_path))
            if after.open_step_path
            else Edit("close_step")
        )
    path = before.open_step_path or ""
    old_values, new_values = before_draft.values(path), after_draft.values(path)
    form = ObjectForm(before_draft.schema(path), {})
    [(name, value)] = [(name, value) for name, value in new_values.items() if old_values.get(name) != value]
    if name in form.layout.rows:
        rows = old_values.get(name, [])
        if len(value) > len(rows):
            return Edit("add_row", name)
        [(index, key, text)] = [
            (index, key, text)
            for index, (old_row, new_row) in enumerate(zip(rows, value, strict=True))
            for key, text in new_row.items()
            if old_row.get(key) != text
        ]
        return Edit("type", f"{name}-{index}-{key}", str(text))
    field = form.fields[name]
    if isinstance(field, ListField):
        return Edit("add_tag", name, value[-1])
    if isinstance(field, forms.ChoiceField):
        options = [(str(choice), str(label)) for choice, label in cast(list[tuple[Any, str]], field.choices)]
        return Edit("select", name, str(value), tuple(options) if isinstance(field.widget, forms.Select) else ())
    return Edit("type", name, str(field.widget.format_value(value)))


def editor_stages(stages: list[EditingStage], question_set: QuestionSet) -> list[EditorStageView]:
    """Each stage of the mark scheme in the editor, a step showing only the fields it changes."""
    drafts = [MarkingDraft(stage.marking, question_set, stage.marking.get("question")) for stage in stages]
    shown_fields: dict[str, set[str]] = {}
    for stage, draft in zip(stages, drafts, strict=True):
        if stage.open_step_path:
            shown_fields.setdefault(stage.open_step_path, set()).update(draft.values(stage.open_step_path))
    edits = [
        edit_between(*pair, *draft_pair) for pair, draft_pair in zip(pairwise(stages), pairwise(drafts), strict=True)
    ]
    return [
        EditorStageView(
            editor(HeroWorkspace(draft, f"hero-{index}", shown_fields.get(path, set())), path),
            edit,
        )
        for index, (path, draft, edit) in enumerate(
            zip((stage.open_step_path or "" for stage in stages), drafts, [*edits, None], strict=True)
        )
    ]


def hero_view() -> HeroView:
    content = landing_content().hero
    question = unsaved_question(content.question_set, content.question_key)
    core_question = question.core_question
    max_marks = core_question.max_marks
    adjustment = Adjustment(marks=content.adjustment.marks, feedback=content.adjustment.feedback)
    draft = MarkingDraft(content.editing_stages[-1].marking, content.question_set, content.question_key)
    summary = summary_of(draft.finalized())
    results = {
        answer.text: language_model_result(answer.result, max_marks, summary.method_label) for answer in content.answers
    }

    def student_answers_for(answer: HeroAnswer, *, marked: bool = True, adjusted: bool = False) -> list[StudentAnswer]:
        is_adjusted = adjusted and answer.text == content.adjustment.answer
        result = results[answer.text] if marked else None
        return student_answers(core_question, answer, result, adjustment if is_adjusted else None)

    def answer_groups(*, marked: bool = True, adjusted: bool = False) -> list[AnswerGroup]:
        """A group for each answer, as many as the mockup shows."""
        groups = [
            AnswerGroup(student_answers_for(answer, marked=marked, adjusted=adjusted)) for answer in content.answers
        ]
        return largest_first(groups)[:SHOWN_GROUP_COUNT]

    def average_proportion(*, adjusted: bool) -> float:
        return (
            fmean(
                entry.marks or 0
                for answer in content.answers
                for entry in student_answers_for(answer, adjusted=adjusted)
            )
            / max_marks
        )

    unmarked = answer_groups(marked=False)
    adjusted_groups = answer_groups(adjusted=True)
    [adjusted_group_index] = [index for index, group in enumerate(adjusted_groups) if group.adjusted_count]

    labels = {other.question_key: BUILTINS.find(other.method_ref).label for other in content.other_questions}
    labels[content.question_key] = summary.method_label
    averages = {other.question_key: other.average_percent / 100 for other in content.other_questions}

    def question_list(progress: float | None = None, proportion: float | None = None) -> list[ListedQuestion]:
        """The questions not marked, Q1's marking at `progress`; or all marked, Q1's average `proportion`."""
        is_marked = proportion is not None
        proportions = averages | {content.question_key: proportion}
        return [
            ListedQuestion(
                question=Question(assessment_id=ASSESSMENT_ID, key=key, position=position),
                is_marked=is_marked,
                badge=marking_badge(labels[key] if is_marked else None, has_problems=False, has_imported_marks=False),
                proportion=proportions[key] if is_marked else None,
                unmarked_count=0,
                progress=progress if key == content.question_key else None,
                has_draft=False,
                is_shown=key == content.question_key,
                failure="",
            )
            for position, key in enumerate(content.question_set)
        ]

    def marks_cell(key: str, marks: float, *, adjusted: bool = False) -> MarksCell:
        return MarksCell(marks, level_of(marks, marks, content.question_set[key].max_marks), adjusted)

    students = []
    for student in content.students:
        is_adjusted = student.answer == content.adjustment.answer
        marked_question_marks = content.adjustment.marks if is_adjusted else results[student.answer].marks
        cells = [marks_cell(content.question_key, marked_question_marks, adjusted=is_adjusted)]
        cells += [marks_cell(key, marks) for key, marks in student.marks.items()]
        students.append(StudentRow(student.student_id, sum(cell.marks for cell in cells), cells))

    position = Position(content.question_set, content.question_key, is_in_group=False)
    offered = choices(compatible_methods(position), NO_SAVED_METHODS)
    [steps_setting] = draft.settings_holding_steps("")
    return HeroView(
        content=content,
        question=question,
        student_count=sum(answer.student_count for answer in content.answers),
        total_max_marks=sum(core_question.max_marks for core_question in content.question_set.values()),
        question_lists=[
            question_list(),
            question_list(progress=STARTING_RUN.progress),
            question_list(proportion=average_proportion(adjusted=False)),
            question_list(proportion=average_proportion(adjusted=True)),
        ],
        header_columns=[
            AnswersColumn(question, "answers", unmarked),
            AnswersColumn(question, "answers", unmarked, marking_job=STARTING_RUN),
        ],
        answer_group_lists=[unmarked, answer_groups(), adjusted_groups],
        adjusted_group_index=adjusted_group_index,
        everyday=offered.everyday,
        advanced_choices=offered.advanced,
        step_choices=choices(draft.methods(steps_setting.new_step_path), NO_SAVED_METHODS).everyday,
        picker_title=PickerForm(action="").title,
        editor_stages=editor_stages(content.editing_stages, content.question_set),
        summary=summary,
        students=students,
        sending_summary=Sending(MarksMode.MARKS, None, include_feedback=True, override_count=0).summary,
    )
