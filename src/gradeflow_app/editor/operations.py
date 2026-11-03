"""What an editor's buttons do to a draft: open, change the method of, update, add, remove and move checks, add and
remove rows, and read fields."""

from collections.abc import Callable

from django.http import QueryDict
from gradeflow_core.editing import EditingError, rewrite_references

from gradeflow_app.editor.editing import CheckWorkspace
from gradeflow_app.ui.schemaforms.forms import edit_collection

type Operation = Callable[[CheckWorkspace, str, QueryDict], str]


def apply(workspace: CheckWorkspace, data: QueryDict, path: str) -> str:
    """Apply the posted fields and operation in `operation` to the check at `path`; returns the path to show next. An
    operation the core refuses, as on a check or row that's gone after a double click or an edit in another tab, or
    with a method it doesn't offer there, changes nothing."""
    if not workspace.checks.is_check(path):
        return ""
    name = str(data.get("operation", ""))
    if "fields_posted" in data and name not in ("open", "change_method"):
        form = workspace.form(path, data=data)
        form.full_clean()
        workspace.checks.update(path, form.values())
    operation = _OPERATIONS.get(name)
    if operation is None:
        return path
    try:
        return operation(workspace, path, data)
    except EditingError:
        return path


def _target(data: QueryDict) -> str:
    return str(data.get("target", ""))


def _open(workspace: CheckWorkspace, path: str, data: QueryDict) -> str:
    return _target(data) if workspace.checks.is_check(_target(data)) else path


def _change_method(workspace: CheckWorkspace, path: str, data: QueryDict) -> str:
    workspace.checks.change_method(_target(data), str(data.get("method", "")))
    return _target(data)


def _add(workspace: CheckWorkspace, path: str, data: QueryDict) -> str:
    """Add a check of the posted method at `target`, where a setting that holds checks takes a new one, reading the
    posted question if any."""
    question = str(data.get("question") or "") or None
    workspace.checks.add(_target(data), str(data.get("method", "")), question)
    return _target(data)


def _remove(workspace: CheckWorkspace, path: str, data: QueryDict) -> str:
    return workspace.checks.remove(_target(data))


def _move(workspace: CheckWorkspace, path: str, data: QueryDict) -> str:
    workspace.checks.move(_target(data), 1 if data.get("direction") == "down" else -1)
    return path


def _use_latest(workspace: CheckWorkspace, path: str, data: QueryDict) -> str:
    """Use the latest version of the method of the check at `target`, wherever the marking uses that version."""
    method = workspace.checks.method(_target(data))
    latest = workspace.offers.newer_version(method, workspace.methods) if method else None
    if method is not None and latest is not None:
        workspace.checks.replace(rewrite_references(workspace.checks.data, {method.ref: latest.ref}))
    return path


def _rows(workspace: CheckWorkspace, path: str, data: QueryDict) -> str:
    target = _target(data)
    current = workspace.checks.values(path).get(target)
    workspace.checks.update(path, {target: edit_collection(workspace.layout(path), target, current, data)})
    return path


_OPERATIONS: dict[str, Operation] = {
    "open": _open,
    "change_method": _change_method,
    "add": _add,
    "remove": _remove,
    "use_latest": _use_latest,
    "move": _move,
    "add_row": _rows,
    "remove_row": _rows,
}
