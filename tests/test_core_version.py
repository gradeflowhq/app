from types import SimpleNamespace

import pytest

from gradeflow_app import core_version


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (None, "0.5.0"),
        (SimpleNamespace(url="file:///core", dir_info=SimpleNamespace(editable=True)), "0.5.0"),
        (SimpleNamespace(url="https://example.com/core.git", vcs_info=SimpleNamespace(commit_id="abc123")), "abc123"),
    ],
)
def test_installed_release_identity(
    monkeypatch: pytest.MonkeyPatch, origin: SimpleNamespace | None, expected: str
) -> None:
    monkeypatch.delenv("GRADEFLOW_CORE_VERSION", raising=False)
    package = SimpleNamespace(version="0.5.0", origin=origin)
    monkeypatch.setattr(core_version, "distribution", lambda _: package)
    assert core_version.core_version() == expected


def test_local_build_can_be_identified_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRADEFLOW_CORE_VERSION", "local-build-42")
    assert core_version.core_version() == "local-build-42"
