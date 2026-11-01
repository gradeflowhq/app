"""The marking implementation release captured by the app and its jobs."""

import json
import os
from importlib.metadata import distribution


def core_build() -> str:
    if configured := os.environ.get("GRADEFLOW_CORE_BUILD"):
        return configured
    package = distribution("gradeflow-core")
    source = json.loads(package.read_text("direct_url.json") or "{}")
    return str(source.get("vcs_info", {}).get("commit_id") or package.version)


CORE_BUILD = core_build()
