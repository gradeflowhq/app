"""A method's workspace: the person's draft of it, or a published version they try, with their test answers."""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.urls import reverse
from gradeflow_core import GradeFlowError, Methods, QuestionSet, Step
from gradeflow_core import Method as CoreMethod
from gradeflow_core.editing import FailedFile, LoadedFiles, MarkingDraft, MethodDraft, RowResult
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.models import Course
from gradeflow_app.editor.editing import EditorActions, StepWorkspace
from gradeflow_app.jobs import runs
from gradeflow_app.jobs.payloads import TestRun
from gradeflow_app.libraries.collection import (
    OfferedMethods,
    files_needed_by,
    loaded_usable_methods,
    offers,
    usable,
)
from gradeflow_app.libraries.method_tests import MethodTests, examples_of, questions_of, rows_of
from gradeflow_app.libraries.models import Method, MethodDraftRecord, MethodVersion, VersionTests
from gradeflow_app.libraries.services import method_draft, publish, version_name
from gradeflow_app.ui.schemaforms.forms import ObjectForm
from gradeflow_app.ui.schemaforms.layout import unrepresentable_fields
from gradeflow_app.ui.schemaforms.schema import labels_of, resolve, unwrap_nullable

DRAFT_ONLY_VIEWS = frozenset({"method_choices", "method_field", "method_publish", "method_discard"})
DRAFT_TESTS = "draft-tests"
VERSION_TESTS = "version-tests"
"""The kinds of run that mark a draft's and a version's test answers."""
_RESULTS_ADAPTER = TypeAdapter(list[RowResult])


class StaleWorkspace(Exception):
    """The workspace changed in another tab since the page was shown."""


@dataclass
class MethodWorkspace(StepWorkspace):
    method: Method
    tests_record: MethodDraftRecord | VersionTests

    @property
    def draft_record(self) -> MethodDraftRecord | None:
        return self.tests_record if isinstance(self.tests_record, MethodDraftRecord) else None

    @property
    def version(self) -> MethodVersion | None:
        """The published version shown, or the one the draft started from."""
        return self.tests_record.version if isinstance(self.tests_record, VersionTests) else self.tests_record.based_on

    @property
    def editable(self) -> bool:
        return self.draft_record is not None

    @property
    def course(self) -> Course | None:
        return self.method.library.course

    @cached_property
    def offers(self) -> OfferedMethods:
        return offers(self.tests_record.user, self.course)

    @property
    def definition(self) -> str:
        return (
            self.tests_record.definition
            if isinstance(self.tests_record, MethodDraftRecord)
            else self.tests_record.version.definition
        )

    @cached_property
    def tests(self) -> MethodTests:
        tests: MethodTests = deepcopy(self.tests_record.tests)
        return tests

    @cached_property
    def questions(self) -> QuestionSet:
        return questions_of(self.tests)

    @cached_property
    def loaded(self) -> LoadedFiles:
        return loaded_usable_methods(self.tests_record.user, self.course)

    @cached_property
    def methods(self) -> Methods:
        return self.loaded.methods

    @property
    def failed(self) -> FailedFile | None:
        """Why the core can't load the version shown, if it can't."""
        if self.draft_record or self.version is None:
            return None
        return self.loaded.failed.get(self.version.ref)

    @cached_property
    def failed_version_name(self) -> str:
        return version_name(self.failed.ref) if self.failed else ""

    @cached_property
    def draft(self) -> MethodDraft:
        number = self.tests_record.version.number if isinstance(self.tests_record, VersionTests) else None
        test_values = self.tests.get("test_values", {})
        return method_draft(
            self.method, self.definition, self.tests, self.methods, version=number, test_values=test_values
        )

    @cached_property
    def changed(self) -> bool:
        """Whether the draft's method differs from the version it's based on, if any."""
        base = self.draft_record.based_on if self.draft_record else None
        if base is None:
            return True
        published = method_draft(self.method, base.definition, self.tests, self.methods, version=self.draft.version)
        return published.to_yaml() != self.draft.to_yaml()

    @property
    def defaults(self) -> dict[str, Any]:
        """Each declared setting's default, where it has one."""
        return {
            name: declaration["default"]
            for name, declaration in self.draft.declared_settings.items()
            if "default" in declaration
        }

    @property
    def marking_draft(self) -> MarkingDraft:
        return self.draft.steps_draft

    def url(self, name: str) -> str:
        """The workspace's own address for the view `name`."""
        kwargs: dict[str, Any] = {"method": self.method.pk}
        if isinstance(self.tests_record, VersionTests) and name not in DRAFT_ONLY_VIEWS:
            kwargs["number"] = self.tests_record.version.number
        return reverse(name, kwargs=kwargs)

    @property
    def actions(self) -> EditorActions:
        return EditorActions(
            update=self.url("method_step"),
            methods=self.url("method_choices") if self.editable else "",
        )

    def form(self, path: str, **kwargs: Any) -> ObjectForm:
        form = super().form(path, **kwargs)
        if not self.editable:
            form.disable()
            return form
        fields, declared_settings = self.draft.configurable_fields(), self.draft.declared_settings
        use_counts = Counter(field.bound_setting for field in fields if field.bound_setting)
        for field in fields:
            if field.step_path == path:
                context = {
                    "field": field,
                    "declared_setting": declared_settings.get(field.bound_setting or "", {}),
                    "default": self.default_label(field.bound_setting) if field.bound_setting else None,
                    "setting_is_shared": use_counts[field.bound_setting or ""] > 1,
                    "compatible_settings": [
                        (name, declared_settings[name].get("title") or name, use_counts[name])
                        for name in self.draft.compatible_settings(field.path)
                    ],
                    "action": self.url("method_field"),
                }
                form.add_label_controls(field.key, render_to_string("methods/field_source_menu.html", context))
                if field.bound_setting:
                    context["settings_url"] = self.url("method_settings")
                    form.replace_control(field.key, render_to_string("methods/field_filled_by_setting.html", context))
        return form

    @property
    def summary(self) -> str:
        """What the method marks, in the core's words."""
        return self.marking_draft.describe("")

    @cached_property
    def yaml_only_reasons(self) -> list[str]:
        """Why the steps can only be edited as YAML, if they can't be edited field by field."""
        if reasons := self.draft.uneditable_reasons:
            return reasons
        try:
            shown = [
                title
                for path in self.marking_draft.paths()
                for title in unrepresentable_fields(self.marking_draft.schema(path), self.marking_draft.values(path))
            ]
        except GradeFlowError as error:
            return [str(error)]
        return list(dict.fromkeys(f"{title} has a value only YAML can show." for title in shown))

    def default_label(self, name: str) -> Any:
        """A declared setting's default as people read it: a choice by its label."""
        schema = self.draft.declared_setting_schema(name)
        default, _ = unwrap_nullable(resolve(schema["properties"].get("default", {}), schema.get("$defs", {})))
        return as_labels(self.draft.declared_settings[name].get("default"), labels_of(default))

    def fields_filled_by(self, name: str) -> list[tuple[str, str]]:
        """The fields a declared setting fills: each one's step path and label."""
        fields = [field for field in self.draft.configurable_fields() if field.bound_setting == name]
        step_paths = list(dict.fromkeys(field.step_path for field in fields))
        labels = dict(zip(step_paths, self.marking_draft.numbered_labels(step_paths), strict=True))
        return [(field.step_path, f"{labels[field.step_path]} · {field.title}") for field in fields]

    def current_results(self) -> list[RowResult] | None:
        """The results of the last test run, while they mark what is here now."""
        tests_record = self.tests_record
        return self.last_results() if tests_record.run_revision == tests_record.revision else None

    def last_results(self) -> list[RowResult] | None:
        """The results of the last test run, even if what is here has changed since, to show dimmed."""
        if self.tests_record.results is None:
            return None
        try:
            return _RESULTS_ADAPTER.validate_python(self.tests_record.results)
        except PydanticValidationError:
            return None  # from a build of the core that marks differently

    @cached_property
    def untested(self) -> bool:
        """Whether there are example answers the last test run didn't mark as they are now."""
        return bool(examples_of(self.tests)) and self.current_results() is None

    def record_examples(self) -> None:
        """Give the example answers what the last test run gave them, for the guide to show."""
        if rows := examples_of(self.tests, self.current_results()):
            self.draft.set_example(rows, index=self.tests.get("example_index"))

    def publish(self, user: User) -> MethodVersion:
        """Publish the draft as the method's next version, which starts with the last test run's results if they
        mark the draft as it is. Raises `ValidationError` if the draft can't be published yet."""
        assert self.draft_record is not None
        if not self.changed:
            assert self.version is not None
            raise ValidationError(f"Nothing changed since version {self.version.number}.")
        if not self.valid:
            raise ValidationError(f"Finish the marking first: {self.marking_draft.problems[0].message}")
        if self.untested:
            raise ValidationError("Run the tests first, so the examples show their marks.")
        self.record_examples()
        tested = self.current_results() is not None
        version = publish(self.draft_record, user, self.draft)
        runs.stop(DRAFT_TESTS, self.draft_record.pk)
        if tested:
            tests = version_tests_of(version, user)
            tests.revision = tests.run_revision = self.tests_record.revision
            tests.results, tests.failure = self.tests_record.results, ""
            tests.save(update_fields=["revision", "run_revision", "results", "failure"])
        return version

    def test_run(self) -> TestRun:
        """What a test run of the method marks. Raises `EditingError` if it can't be tried yet."""
        method, methods = self._tested_methods()
        if self.draft_record is None:
            files = list(files_needed_by([method.ref]))
        else:
            files = [*files_needed_by(methods.dependencies(method.ref)), (self.method.full_name, self.draft.to_yaml())]
        return TestRun(
            method=method.ref,
            methods=files,
            questions=self.questions,
            question=self.draft.question,
            settings=self.draft.test_values,
            rows=rows_of(self.tests),
        )

    def tested_step(self) -> Step:
        """The step a test run marks with. Raises `GradeFlowError` if it can't be tried yet."""
        method, methods = self._tested_methods()
        return Step.load({"uses": method.ref, "with": self.draft.test_values}, methods=methods)

    def test_run_asks_models(self) -> bool:
        """Whether a test run asks language models. Raises `GradeFlowError` if it can't be tried yet."""
        return self.tested_step().needs_model_endpoint()

    def _tested_methods(self) -> tuple[CoreMethod[Any], Methods]:
        """The method as tested, and the methods it may use. Raises `EditingError` if it can't be tried yet."""
        method = self.draft.finalized()
        return method, Methods([*(each for each in self.methods if each.ref != method.ref), method])

    def save(self) -> None:
        """Store the edits made to the workspace as its next revision; with none, as after only opening a step,
        it stays as it is, and so do its test results."""
        tests = self.tests
        tests["test_values"] = self.draft.test_values
        definition = self.definition
        if self.draft_record is not None:
            at = tests.get("example_index")
            if at is not None and at >= len(self.draft.examples):
                at = None  # since removed from the YAML
            if at is not None or examples_of(tests):
                tests["example_index"] = self.draft.set_example(examples_of(tests), index=at)
            definition = self.draft.to_yaml()
        if tests == self.tests_record.tests and definition == self.definition:
            return
        if self.draft_record is not None:
            self.draft_record.definition = definition
        self.tests_record.save_revision(tests)


def as_labels(value: Any, labels: dict[str, str]) -> Any:
    """A choice by its label, or each of a list of them."""
    if isinstance(value, list):
        return [as_labels(item, labels) for item in value]
    return labels.get(value, value) if isinstance(value, str) else value


def example_index(draft: MethodDraft, tests: MethodTests) -> int | None:
    """Where among the method's examples the one the chosen test answers make is."""
    chosen = [row.answers for row in examples_of(tests)]
    return next(
        (index for index, example in enumerate(draft.examples) if [row.answers for row in example.rows] == chosen),
        None,
    )


def lock_workspace(workspace: MethodWorkspace, revision: str | None) -> MethodWorkspace:
    """The workspace, locked for an edit made from the page showing `revision`."""
    model = type(workspace.tests_record)
    tests_record = model.objects.select_for_update(of=("self",)).get(pk=workspace.tests_record.pk)
    if revision is None or str(tests_record.revision) != revision:
        raise StaleWorkspace
    return MethodWorkspace(workspace.method, tests_record)


def start_draft(method: Method, user: User, version: MethodVersion) -> MethodDraftRecord:
    """The person's draft of the method, started from `version` if they have none, as the core reads it: without
    its examples' results. It is based on the latest version, so starting from an earlier one publishes that one
    again as the next."""
    draft = method_draft(method, version.definition, version.tests, usable(user, method.library.course))
    record, _ = MethodDraftRecord.objects.get_or_create(
        method=method,
        user=user,
        defaults={
            "based_on": method.latest_version,
            "definition": draft.to_yaml(),
            "tests": deepcopy(version.tests),
        },
    )
    return record


def version_tests_of(version: MethodVersion, user: User) -> VersionTests:
    """The user's test answers of `version`, copied from the version's own the first time they're asked for."""
    tests, _ = VersionTests.objects.get_or_create(
        version=version, user=user, defaults={"tests": deepcopy(version.tests)}
    )
    return tests
