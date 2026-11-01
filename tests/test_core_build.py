import json
from types import SimpleNamespace

import pytest

from gradeflow_app import core_build


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (None, "0.5.0"),
        ({"dir_info": {"editable": True}}, "0.5.0"),
        ({"vcs_info": {"commit_id": "abc123"}}, "abc123"),
    ],
)
def test_installed_release_identity(
    monkeypatch: pytest.MonkeyPatch, metadata: dict[str, object] | None, expected: str
) -> None:
    monkeypatch.delenv("GRADEFLOW_CORE_BUILD", raising=False)
    package = SimpleNamespace(version="0.5.0", read_text=lambda _: json.dumps(metadata) if metadata else None)
    monkeypatch.setattr(core_build, "distribution", lambda _: package)
    assert core_build.core_build() == expected


def test_local_build_can_be_identified_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRADEFLOW_CORE_BUILD", "local-build-42")
    assert core_build.core_build() == "local-build-42"
