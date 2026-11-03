from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile

MAX_FILE_BYTES = 20 * 2**20


class UploadForm(forms.Form):
    """What importer.js uploads: the chosen columns as CSV with IDs encrypted, the answer key row if there is one,
    and the original file encrypted."""

    name = forms.CharField(max_length=255)
    answers_csv = forms.FileField()
    student_id_column = forms.CharField(strip=False)
    answer_columns = forms.JSONField(required=False)
    marks_columns = forms.JSONField(required=False)
    answer_key = forms.JSONField(required=False)
    original = forms.FileField(required=False)
    sheet = forms.CharField(required=False, strip=False, max_length=255)
    header_row = forms.IntegerField(required=False, min_value=0)
    first_row = forms.IntegerField(required=False, min_value=0)
    last_row = forms.IntegerField(required=False, min_value=0)

    def clean_answers_csv(self) -> str:
        try:
            text: str = self.cleaned_data["answers_csv"].read().decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValidationError("The file couldn't be read.") from error
        return text

    def clean_answer_columns(self) -> list[str]:
        columns = self.cleaned_data["answer_columns"]
        if not columns or not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
            raise ValidationError("Choose at least one answer column.")
        return columns

    def clean_marks_columns(self) -> dict[str, str]:
        columns = self.cleaned_data["marks_columns"] or {}
        if not isinstance(columns, dict) or not all(isinstance(column, str) for column in columns.values()):
            raise ValidationError("Choose which question each marks column is for.")
        return columns

    def clean_answer_key(self) -> dict[str, str]:
        key = self.cleaned_data["answer_key"] or {}
        columns = self.cleaned_data.get("answer_columns", [])
        if not isinstance(key, dict) or not all(
            column in columns and isinstance(cell, str) for column, cell in key.items()
        ):
            raise ValidationError("The answer key must give a cell for answer columns only.")
        return key

    def clean_original(self) -> bytes | None:
        file = self.cleaned_data["original"]
        return None if file is None else _read_upload(file, "The file is too large to store.")


class QuestionDocumentForm(forms.Form):
    file = forms.FileField(error_messages={"required": "Choose the question document."})

    def clean_file(self) -> bytes:
        return _read_upload(self.cleaned_data["file"], "The question document is too large.")


def _read_upload(file: UploadedFile[bytes], too_large_message: str) -> bytes:
    if file.size is not None and file.size > MAX_FILE_BYTES:
        raise ValidationError(f"{too_large_message} The limit is {MAX_FILE_BYTES // 2**20} MB.")
    data: bytes = file.read()
    return data
