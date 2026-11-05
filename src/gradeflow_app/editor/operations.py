"""What an editor's buttons do to a draft: open, change the method of, update, add, remove and move checks, add and
remove rows, and read fields."""

from django.http import QueryDict
from gradeflow_core.editing import EditingError, rewrite_references

from gradeflow_app.editor.editing import CheckWorkspace
from gradeflow_app.ui.schemaforms.forms import edit_collection
from gradeflow_app.ui.schemaforms.layout import layout


def apply(workspace: CheckWorkspace, data: QueryDict, path: str) -> str:
    """Apply the posted fields and operation in `operation` to the check at `path`; returns the path to show next. An
    operation the core refuses, as on a check or row that's gone after a double click or an edit in another tab, or
    with a method it doesn't offer there, changes nothing."""
    if not workspace.checks.is_check(path):
        return ""
    name = str(data.get("operation", ""))
    try:
        if "fields_posted" in data and name not in ("open", "change_method"):
            form = workspace.form(path, data=data)
            form.full_clean()
            workspace.checks.update(path, form.values())
        return _operate(workspace, name, data, path)
    except EditingError:
        return path


def _operate(workspace: CheckWorkspace, name: str, data: QueryDict, path: str) -> str:
    checks = workspace.checks
    target, method = str(data.get("target", "")), str(data.get("method", ""))
    match name:
        case "open":
            return target if checks.is_check(target) else path
        case "change_method":
            checks.change_method(target, method)
            return target
        case "add":
            checks.add(target, method, str(data.get("question") or "") or None)
            return target
        case "remove":
            return checks.remove(target)
        case "move":
            checks.move(target, 1 if data.get("direction") == "down" else -1)
        case "use_latest":
            current = checks.method(target)
            latest = workspace.offers.newer_version(current, workspace.methods) if current else None
            if current is not None and latest is not None:
                checks.replace(rewrite_references(checks.data, {current.ref: latest.ref}))
        case "add_row" | "remove_row":
            rows = edit_collection(layout(checks.schema(path)), target, checks.values(path).get(target), data)
            checks.update(path, {target: rows})
    return path
