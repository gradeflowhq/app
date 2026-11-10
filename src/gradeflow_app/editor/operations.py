"""What an editor's buttons do to a draft: open, change the method of, update, add, remove and move steps, add and
remove rows, and read fields."""

from django.http import QueryDict
from gradeflow_core.editing import EditingError, rewrite_references

from gradeflow_app.editor.editing import StepWorkspace
from gradeflow_app.ui.schemaforms.forms import edit_collection
from gradeflow_app.ui.schemaforms.layout import layout


def apply(workspace: StepWorkspace, data: QueryDict, path: str) -> str:
    """Apply the posted fields and operation in `operation` to the step at `path`; returns the path to show next. An
    operation the core refuses, as on a step or row that's gone after a double click or an edit in another tab, or
    with a method it doesn't offer there, changes nothing."""
    if not workspace.marking_draft.is_step(path):
        return ""
    name = str(data.get("operation", ""))
    try:
        if "fields_posted" in data and name not in ("open", "change_method"):
            form = workspace.form(path, data=data)
            form.full_clean()
            workspace.marking_draft.update(path, form.values())
        return _operate(workspace, name, data, path)
    except EditingError:
        return path


def _operate(workspace: StepWorkspace, name: str, data: QueryDict, path: str) -> str:
    marking_draft = workspace.marking_draft
    target, method = str(data.get("target", "")), str(data.get("method", ""))
    match name:
        case "open":
            return target if marking_draft.is_step(target) else path
        case "change_method":
            marking_draft.change_method(target, method)
            return target
        case "add":
            marking_draft.add(target, method, str(data.get("question") or "") or None)
            return target
        case "remove":
            return marking_draft.remove(target)
        case "move":
            marking_draft.move(target, 1 if data.get("direction") == "down" else -1)
        case "use_latest":
            current = marking_draft.method(target)
            latest = workspace.offers.newer_version(current, workspace.methods) if current else None
            if current is not None and latest is not None:
                marking_draft.replace(rewrite_references(marking_draft.data, {current.ref: latest.ref}))
        case "add_row" | "remove_row":
            rows = edit_collection(
                layout(marking_draft.schema(path)), target, marking_draft.values(path).get(target), data
            )
            marking_draft.update(path, {target: rows})
    return path
