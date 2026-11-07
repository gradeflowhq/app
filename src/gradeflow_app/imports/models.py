import uuid

from django.conf import settings
from django.db import models
from django.db.models.functions import Coalesce
from gradeflow_core import Submission

from gradeflow_app.assessments.models import Assessment


class SubmissionsFileManager(models.Manager["SubmissionsFile"]):
    def get_queryset(self) -> models.QuerySet[SubmissionsFile]:
        return super().get_queryset().defer("original", "id_mapping_file")


class SubmissionsFile(models.Model):
    """An uploaded submissions file: the encrypted ID column and the chosen answer and marks columns.

    Rows count from 0 in the sheet as the browser reads it, blank rows included, so they match the spreadsheet.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="submissions_files")
    name = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    student_id_column = models.CharField(max_length=255)
    answer_columns = models.JSONField()
    """Column names, in file order."""
    marks_columns = models.JSONField(default=dict)
    """Marks column name by answer column name."""
    student_count = models.PositiveIntegerField()
    original = models.BinaryField(null=True, editable=False)
    """The whole original file, encrypted in the browser (lib/assessment-crypto.js encryptFile); only the latest file
    keeps it."""
    sheet = models.CharField(max_length=255, blank=True)
    header_row = models.PositiveIntegerField(null=True)
    first_row = models.PositiveIntegerField(null=True)
    last_row = models.PositiveIntegerField(null=True)
    file_pattern = models.CharField(max_length=255, blank=True)
    """The pattern finding a zip's students and files."""
    file_questions = models.JSONField(default=dict)
    """Question by a zip's top-level file or folder name; "" for none."""
    id_mapping = models.JSONField(null=True)
    """The ID mapping file's `file_name`, the `match_column` with the file's IDs and the `id_column` replacing them."""
    id_mapping_file = models.BinaryField(null=True, editable=False)
    """The ID mapping file, encrypted in the browser like `original`."""

    objects = SubmissionsFileManager()

    class Meta:
        ordering = ("-uploaded_at",)

    def __str__(self) -> str:
        return self.name


class Student(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="students")
    encrypted_id = models.CharField(max_length=255)
    answers = models.JSONField()
    """Raw answer by question key."""
    imported_marks = models.JSONField(default=dict)
    """Marks from the submissions file by question key."""

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("assessment", "encrypted_id"), name="one_student_per_encrypted_id"),
        )
        ordering = ("id",)

    def __str__(self) -> str:
        return self.encrypted_id

    @property
    def submission(self) -> Submission:
        """The student's answers as the core reads them."""
        return Submission(student_id=self.encrypted_id, answers=self.answers)


def answer_expression(key: str) -> models.Func:
    """A student's answer to question `key`, read alone; blank if none. Unlike `KT`, it reads any key as one: `KT`
    takes `1` for an array index and `a__b` for a path."""
    return Coalesce(
        models.Func("answers", models.Value(key), function="jsonb_extract_path_text"),
        models.Value(""),
        output_field=models.TextField(),
    )


def imported_mark_keys(assessment: Assessment) -> set[str]:
    """The questions any student has marks for in the submissions file."""
    keys = models.Func("imported_marks", function="jsonb_object_keys", output_field=models.TextField())
    return set(assessment.students.order_by().annotate(question=keys).values_list("question", flat=True).distinct())


def has_imported_marks(assessment: Assessment, key: str) -> bool:
    """Whether any student has marks for question `key` in the submissions file."""
    return assessment.students.filter(imported_marks__has_key=key).exists()
