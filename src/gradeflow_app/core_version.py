"""The core version captured by the app and its jobs: the installed core's commit, or its version for a release."""

import json
import os
from importlib.metadata import distribution


def core_version() -> str:
    if configured := os.environ.get("GRADEFLOW_CORE_VERSION"):
        return configured
    package = distribution("gradeflow-core")
    source = json.loads(package.read_text("direct_url.json") or "{}")
    return str(source.get("vcs_info", {}).get("commit_id") or package.version)


CORE_VERSION = core_version()
