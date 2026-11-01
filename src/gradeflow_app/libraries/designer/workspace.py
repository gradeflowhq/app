"""A method's workspace: the person's draft of it, or a published version they try, with their test answers."""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from gradeflow_core import GradeFlowError, Methods, ModelEndpoint, QuestionSet
from gradeflow_core.editing import MarkingDraft, RowResult
from gradeflow_core.editing import MethodDraft as CoreDraft

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.models import Course
from gradeflow_app.editor.editing import CheckWorkspace, EditorActions
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import TestRun
from gradeflow_app.libraries.collection import Offers, needed, offers, usable
from gradeflow_app.libraries.models import Method, MethodDraft, MethodVersion, VersionTests
from gradeflow_app.libraries.services import draft_of
from gradeflow_app.libraries.workbench import Tests, examples_of, questions_of, rows_of
from gradeflow_app.ui.schemaforms.forms import ObjectForm
from gradeflow_app.ui.schemaforms.layout import unrepresentable
from gradeflow_app.ui.schemaforms.schema import labels_of, resolve, unwrap_nullable

DRAFT_ONLY = frozenset({"method_picker", "method_field", "method_publish", "method_discard"})


class Stale(Exception):
    """The workspace changed in another tab since the page was shown."""


@dataclass
class MethodWorkspace(CheckWorkspace):
    method: Method
    session: MethodDraft | VersionTests

    @property
    def draft(self) -> MethodDraft | None:
        return self.session if isinstance(self.session, MethodDraft) else None

    @property
    def version(self) -> MethodVersion | None:
        """The published version shown, or the one the draft started from."""
        return self.session.version if isinstance(self.session, VersionTests) else self.session.based_on

    @property
    def editable(self) -> bool:
        return self.draft is not None

    @property
    def course(self) -> Course | None:
        return self.method.library.course

    @cached_property
    def offers(self) -> Offers:
        return offers(self.session.user, self.course)

    @property
    def definition(self) -> str:
        return self.session.definition if isinstance(self.session, MethodDraft) else self.session.version.definition

    @cached_property
    def tests(self) -> Tests:
        tests: Tests = deepcopy(self.session.tests)
        return tests

    @cached_property
    def questions(self) -> QuestionSet:
        return questions_of(self.tests)

    @cached_property
    def methods(self) -> Methods:
        return usable(self.session.user, self.course)

    @cached_property
    def core(self) -> CoreDraft:
        number = None if self.draft else self.session.version.number  # type: ignore[union-attr]
        test_values = self.tests.get("test_values", {})
        return draft_of(self.method, self.definition, self.tests, self.methods, version=number, test_values=test_values)

    @cached_property
    def changed(self) -> bool:
        """Whether the draft's method differs from the version it's based on, if any."""
        base = self.draft.based_on if self.draft else None
        if base is None:
            return True
        published = draft_of(self.method, base.definition, self.tests, self.methods, version=self.core.version)
        return published.to_yaml() != self.core.to_yaml()

    @property
    def defaults(self) -> dict[str, Any]:
        """Each input's default, where it has one."""
        return {name: spec["default"] for name, spec in self.core.inputs.items() if "default" in spec}

    @property
    def checks(self) -> MarkingDraft:
        return self.core.checks

    def url(self, name: str) -> str:
        """The workspace's own address for the view `name`."""
        kwargs: dict[str, Any] = {"method": self.method.pk}
        if isinstance(self.session, VersionTests) and name not in DRAFT_ONLY:
            kwargs["number"] = self.session.version.number
        return reverse(name, kwargs=kwargs)

    @property
    def actions(self) -> EditorActions:
        return EditorActions(
            update=self.url("method_check"),
            methods=self.url("method_picker") if self.editable else "",
            sync="#method-work:queue all",
            include="#method-state",
        )

    def form(self, path: str, **kwargs: Any) -> ObjectForm:
        form = super().form(path, **kwargs)
        if not self.editable:
            form.disable()
            return form
        fields, inputs = self.core.fields(), self.core.inputs
        used = {field.input: sum(other.input == field.input for other in fields) for field in fields if field.input}
        for field in fields:
            if field.check == path:
                context = {
                    "field": field,
                    "input": inputs.get(field.input or "", {}),
                    "default": self.shown_default(field.input) if field.input else None,
                    "used": used.get(field.input or "", 0),
                    "compatible": [
                        (name, inputs[name].get("title") or name, used.get(name, 0))
                        for name in self.core.compatible_inputs(field.path)
                    ],
                    "action": self.url("method_field"),
                }
                form.adorn(field.key, render_to_string("methods/configurable.html", context))
                if field.input:
                    context["inputs_url"] = self.url("method_inputs")
                    form.fill(field.key, render_to_string("methods/filled.html", context))
        return form

    @property
    def summary(self) -> str:
        """What the method marks, in the core's words."""
        return self.checks.describe("")

    @cached_property
    def uneditable(self) -> list[str]:
        """Why the checks can only be edited as YAML, if they can't be edited field by field."""
        if found := self.core.uneditable:
            return found
        try:
            shown = [
                title
                for path in self.checks.paths()
                for title in unrepresentable(self.checks.schema(path), self.checks.values(path))
            ]
        except GradeFlowError as error:
            return [str(error)]
        return list(dict.fromkeys(f"{title} has a value only YAML can show." for title in shown))

    def shown_default(self, name: str) -> Any:
        """An input's default as people read it: a choice by its label."""
        schema = self.core.input_schema(name)
        default, _ = unwrap_nullable(resolve(schema["properties"].get("default", {}), schema.get("$defs", {})))
        return labelled(self.core.inputs[name].get("default"), labels_of(default))

    def uses(self, name: str) -> list[tuple[str, str]]:
        """The settings an input fills: each one's check, and where it is. Checks labelled alike are numbered in
        order."""
        fields = [field for field in self.core.fields() if field.input == name]
        labels = {check: self.label(check) for check in dict.fromkeys(field.check for field in fields)}
        alike, places = Counter(labels.values()), Counter[str]()
        for check, label in labels.items():
            if alike[label] > 1:
                places[label] += 1
                labels[check] = f"{label} {places[label]}"
        return [(field.check, f"{labels[field.check]} · {field.title}") for field in fields]

    def results(self) -> list[RowResult] | None:
        """The results of the last test run, while they mark what is here now."""
        job = self.session.run
        return self.last_results() if job is not None and job.revision == self.session.revision else None

    def last_results(self) -> list[RowResult] | None:
        """The results of the last test run, even if what is here has changed since, to show dimmed."""
        job = self.session.run
        if job is None or job.status != Job.Status.COMPLETED or not job.by_this_core:
            return None
        return [RowResult.model_validate(row) for row in job.result or []]

    @cached_property
    def untested(self) -> bool:
        """Whether there are example answers the last test run didn't mark as they are now."""
        return bool(examples_of(self.tests)) and self.results() is None

    def record_examples(self) -> None:
        """Give the example answers what the last test run gave them, for the guide to show."""
        if rows := examples_of(self.tests, self.results()):
            self.core.set_example(rows, at=self.tests.get("example_at"))

    def test_run(self, model_endpoint: ModelEndpoint | None = None) -> TestRun:
        """What a test run of the method marks, asking language models at `model_endpoint` if it does. Raises
        `EditingError` if it can't be tried yet."""
        method = self.core.finalized()
        if self.draft is None:
            files = list(needed([method.ref]))
        else:
            others = [item for item in self.methods if item.ref != method.ref]
            dependencies = Methods([*others, method]).dependencies(method.ref)
            files = [*needed(dependencies), (self.method.full_name, self.core.to_yaml())]
        return TestRun(
            method=method.ref,
            methods=files,
            questions=self.questions,
            question=self.core.question,
            inputs=self.core.test_values,
            rows=rows_of(self.tests),
            model_endpoint=model_endpoint if method.needs_model_endpoint else None,
        )

    def save(self) -> None:
        """Store the edits made to the workspace as its next revision; with none, as after only opening a check,
        it stays as it is, and so do its test results."""
        tests = self.tests
        tests["test_values"] = self.core.test_values
        definition = self.definition
        if self.draft is not None:
            at = tests.get("example_at")
            if at is not None and at >= len(self.core.examples):
                at = None  # since removed from the YAML
            if at is not None or examples_of(tests):
                tests["example_at"] = self.core.set_example(examples_of(tests), at=at)
            definition = self.core.to_yaml()
        if tests == self.session.tests and definition == self.definition:
            return
        if self.draft is not None:
            self.draft.definition = definition
        self.session.revise(tests)


def labelled(value: Any, labels: dict[str, str]) -> Any:
    """A choice by its label, or each of a list of them."""
    if isinstance(value, list):
        return [labelled(item, labels) for item in value]
    return labels.get(value, value) if isinstance(value, str) else value


def example_at(core: CoreDraft, tests: Tests) -> int | None:
    """Where among the method's examples the one the chosen test answers make is."""
    chosen = [row.answers for row in examples_of(tests)]
    return next(
        (at for at, example in enumerate(core.examples) if [row.answers for row in example.rows] == chosen), None
    )


def locked(workspace: MethodWorkspace, revision: str | None) -> MethodWorkspace:
    """The workspace, locked for an edit made from the page showing `revision`."""
    model = type(workspace.session)
    session = model.objects.select_for_update(of=("self",)).get(pk=workspace.session.pk)
    if revision is None or str(session.revision) != revision:
        raise Stale
    return MethodWorkspace(workspace.method, session)


@transaction.atomic
def start_draft(method: Method, user: User, version: MethodVersion) -> MethodDraft:
    """The person's draft of the method, started from `version` if they have none, as the core reads it: without
    its examples' results. It is based on the latest version, so starting from an earlier one publishes that one
    again as the next."""
    read = draft_of(method, version.definition, version.tests, usable(user, method.library.course))
    draft, _ = MethodDraft.objects.get_or_create(
        method=method,
        user=user,
        defaults={
            "based_on": method.latest_version,
            "definition": read.to_yaml(),
            "tests": deepcopy(version.tests),
        },
    )
    return draft


@transaction.atomic
def version_tests_of(version: MethodVersion, user: User) -> VersionTests:
    tests, _ = VersionTests.objects.get_or_create(
        version=version, user=user, defaults={"tests": deepcopy(version.tests)}
    )
    return tests
