# The image Nomad jobs run: the core and its `ml` extra, its language models, and sandbox.py.
# Build it from the app's root with `-f sandbox/Dockerfile .`; add `--build-context core=<checkout>` to mark with
# a core checkout instead of the core on GitHub.
FROM scratch AS core

FROM python:3.14-slim AS build

# uv needs git for the core, which comes from GitHub.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/sandbox

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=from=core,target=/core,rw \
    if [ -e /core/pyproject.toml ]; then \
        uv venv /opt/sandbox \
        && uv pip install --python /opt/sandbox --no-sources --group pyproject.toml:sandbox "gradeflow-core[ml,llm] @ /core"; \
    else \
        uv sync --only-group sandbox; \
    fi

FROM python:3.14-slim

RUN useradd --create-home --uid 10001 appuser && install -d -o appuser /opt/gradeflow-cache
COPY --from=build /opt/sandbox /opt/sandbox
COPY sandbox/sandbox.py /app/sandbox.py
ENV PATH=/opt/sandbox/bin:$PATH GRADEFLOW_CACHE_DIR=/opt/gradeflow-cache
USER appuser

# Jobs have no network beyond the app, so the models come with the image.
RUN python -m gradeflow_core setup
ENV HF_HUB_OFFLINE=1
