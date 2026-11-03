"""The core version captured by the app and its jobs: the installed core's commit, or its version for a release."""

import os
from importlib.metadata import distribution


def core_version() -> str:
    if configured := os.environ.get("GRADEFLOW_CORE_VERSION"):
        return configured
    package = distribution("gradeflow-core")
    vcs_info = getattr(package.origin, "vcs_info", None)
    return str(vcs_info.commit_id) if vcs_info else package.version


CORE_VERSION = core_version()
