from functools import partial
from typing import Any

from django import forms

from gradeflow_app.publish.models import AssessmentCanvas, CourseCanvas, MarksMode, SentMarks
from gradeflow_app.ui.schemaforms.widgets import Segmented
from gradeflow_app.ui.templatetags.ui import marks

rounding_field = partial(
    forms.FloatField,
    label="Round to the nearest",
    required=False,
    min_value=0,
    error_messages={"min_value": "Round to 0 or more."},
)

CANVAS_FORM_LABELS = {"marks_mode": "Send as", "include_feedback": "Send feedback"}


class CourseCanvasForm(forms.ModelForm[CourseCanvas]):
    """The Canvas course a course's marks go to, and how they're sent."""

    rounding = rounding_field(widget=forms.NumberInput(attrs={"placeholder": "0.5", "class": "max-w-40"}))

    class Meta:
        model = CourseCanvas
        fields = (
            "canvas_course",
            "canvas_course_name",
            "canvas_assignment_group",
            "canvas_assignment_group_name",
            "marks_mode",
            "rounding",
            "include_feedback",
        )
        labels = CANVAS_FORM_LABELS
        help_texts = {"include_feedback": "As a comment with each student's marks. Feedback shows the correct answers."}
        widgets = {"marks_mode": Segmented}


class AssessmentCanvasForm(forms.ModelForm[AssessmentCanvas]):
    """An assessment's Canvas assignment, and how its marks are sent where that differs from its course."""

    assignment = forms.CharField(max_length=50, required=False, widget=forms.HiddenInput)
    rounding = rounding_field()

    class Meta:
        model = AssessmentCanvas
        fields = ("marks_mode", "rounding", "include_feedback")
        labels = CANVAS_FORM_LABELS

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        course = CourseCanvas.of(self.instance.assessment.course)
        self.initial.setdefault("assignment", self.instance.canvas_assignment)
        as_course = "As the course ({})".format
        mode = as_course(course.get_marks_mode_display().lower())
        self.fields["marks_mode"].widget = forms.Select(choices=[("", mode), *MarksMode.choices])
        feedback = forms.NullBooleanSelect()
        feedback.choices = [
            ("unknown", as_course("yes" if course.include_feedback else "no")),
            ("true", "Yes"),
            ("false", "No"),
        ]
        self.fields["include_feedback"].widget = feedback
        rounding = f"{marks(course.rounding)}, as the course" if course.rounding else "As the course"
        self.fields["rounding"].widget.attrs["placeholder"] = rounding
        for name in self.Meta.fields:
            self.fields[name].initial = ""  # Not "(optional)": each falls back to the course.

    def save(self, commit: bool = True) -> AssessmentCanvas:
        self.instance.canvas_assignment = self.cleaned_data["assignment"]
        return super().save(commit)


class SentMarksForm(forms.ModelForm[SentMarks]):
    """What the browser reports sending to Canvas: the assignment, and how many students."""

    class Meta:
        model = SentMarks
        fields = ("assignment_name", "student_count")
