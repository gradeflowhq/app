# The web app's image. Its default command migrates and serves on port 8000;
# `python manage.py db_worker` runs the task worker and `python manage.py job_worker`
# the sandbox job worker from the same image.
FROM python:3.14-slim AS build

# uv needs git for the core, which comes from GitHub.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-dev --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --no-dev
RUN GRADEFLOW_SECRET_KEY=build GRADEFLOW_STATIC_ROOT=/app/staticfiles sh -c \
    ".venv/bin/python manage.py tailwind build && .venv/bin/python manage.py collectstatic --noinput -v 0" \
    && rm -rf .django_tailwind_cli && chmod -R a+rX /app

FROM python:3.14-slim

RUN useradd --create-home --uid 10001 gradeflow
WORKDIR /app
COPY --from=build /app /app

# The sandbox image built from the same commit, which CI passes in.
ARG SANDBOX_IMAGE=ghcr.io/gradeflowhq/sandbox:latest
ARG CORE_VERSION=""
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    GRADEFLOW_STATIC_ROOT=/app/staticfiles \
    GRADEFLOW_NOMAD_IMAGE=$SANDBOX_IMAGE \
    GRADEFLOW_CORE_VERSION=$CORE_VERSION \
    FORWARDED_ALLOW_IPS=* \
    UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=5

USER gradeflow
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py createcachetable && exec uvicorn gradeflow_app.asgi:application --host 0.0.0.0 --port 8000"]
