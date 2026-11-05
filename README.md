# GradeFlow app

The web app for GradeFlow, built with Django and HTMX on [GradeFlow core](https://github.com/gradeflowhq/core). Educators import their students' answers, mark each question, review answers in groups and send the marks to Canvas. Student IDs are encrypted everywhere except in the browser.

## Development

You need Python 3.14 with uv, Docker, Node for the browser-module tests, and a [Nomad agent](#nomad), version 2.0.3 or later.

```sh
docker run -d --name gradeflow-postgres --restart unless-stopped -p 127.0.0.1:5434:5432 \
  -e POSTGRES_USER=gradeflow -e POSTGRES_PASSWORD=gradeflow -e POSTGRES_DB=gradeflow postgres:18-alpine
cp dev.env.example dev.env
uv sync
. ./dev.env
uv run manage.py migrate
uv run manage.py createcachetable
```

Run these four processes, each in its own shell. Source `dev.env` in every shell first, for the commands further down too.

```sh
uv run manage.py tailwind watch  # downloads the Tailwind CLI on first run
uv run uvicorn gradeflow_app.asgi:application --host 0.0.0.0 --port 8000 --reload \
  --reload-dir src --reload-include '*.html' --reload-include '*.txt'
uv run manage.py db_worker       # sends invitation emails
uv run manage.py job_worker      # follows sandbox jobs in Nomad, and fails those that end without calling back
```

Then sign up at http://localhost:8000. Emails, sign-up codes included, are written to `GRADEFLOW_EMAIL_FILE_PATH`.

### Linting and tests

```sh
uv run ruff check && uv run ruff format --check
uv run mypy
uv run manage.py makemigrations --check --dry-run
uv run pytest
node --test tests/js/*.test.mjs
```

Tests run jobs in-process, so they need PostgreSQL but not Nomad. pytest keeps its test database between runs: pass `--create-db` after adding a migration, and set `GRADEFLOW_TEST_DATABASE` to give a parallel run its own. `uvx pre-commit install` formats and checks each commit with ruff.

### Nomad

Marking and grouping answers by meaning run as Nomad batch jobs in the sandbox image. For development, start an agent with `nomad agent -dev` and the Docker driver, and point `GRADEFLOW_APP_URL_FOR_JOBS` at an address of this machine its containers can reach. Uvicorn listens on all interfaces for that reason.

Jobs pull the sandbox from a registry on port 5055, which runs as a Nomad service job. Submit it once, and again whenever the agent restarts:

```sh
jq -Rs '{JobHCL: ., Canonicalize: true}' sandbox/registry.nomad.hcl \
  | curl -sf --data @- "$GRADEFLOW_NOMAD_URL/v1/jobs/parse" | jq '{Job: .}' \
  | curl -sf --data @- "$GRADEFLOW_NOMAD_URL/v1/jobs"
```

After changing `sandbox/` or the core, rebuild the sandbox and push it to the registry, which this machine reaches at the Docker bridge gateway:

```sh
docker buildx create --name gradeflow --driver docker-container  # once
docker buildx build --builder gradeflow -f sandbox/Dockerfile --build-context core=path/to/core \
  --output type=image,name=172.17.0.1:5055/gradeflow-sandbox:latest,push=true,registry.insecure=true .
```

Leave out `--build-context` to build with the core on GitHub.

### Working on the core

The app follows the core's `main` branch; `uv lock --upgrade-package gradeflow-core` picks up its latest commit. To change both at once, install a core checkout in its place and stop `uv run` from syncing it back:

```sh
uv pip install -e 'path/to/core[llm]'
echo 'export UV_NO_SYNC=1' >> dev.env
```

Add `--reload-dir path/to/core/src/gradeflow_core` to the uvicorn command to reload on core changes.

Previews and meaning comparisons are kept per core version. The version is the installed core's commit, or its version for a release wheel; set `GRADEFLOW_CORE_VERSION` to name a local editable build.

## Configuration

| Variable | Default | |
|---|---|---|
| `GRADEFLOW_SECRET_KEY` | | Required |
| `GRADEFLOW_DEBUG` | off | `true` in development |
| `GRADEFLOW_LOG_LEVEL` | `INFO` | Ignored when debugging |
| `GRADEFLOW_ALLOWED_HOSTS` | `localhost,127.0.0.1` | |
| `GRADEFLOW_CLIENT_IP_HEADER` | | The header in which the proxy passes the client's address, such as `X-Real-IP`, for sign-in rate limits and signed-in devices. Without it, the address comes from `X-Forwarded-For`; if the proxy appends to that header, set `FORWARDED_ALLOW_IPS` to the proxy's addresses |
| `GRADEFLOW_DATABASE_URL` | `postgres://gradeflow:gradeflow@localhost:5434/gradeflow` | |
| `GRADEFLOW_TEST_DATABASE` | `test_gradeflow` | |
| `GRADEFLOW_STATIC_ROOT` | | Where `collectstatic` writes compressed files for WhiteNoise |
| `GRADEFLOW_FROM_EMAIL` | `GradeFlow <noreply@localhost>` | |
| `GRADEFLOW_EMAIL_BACKEND` | console | A Django or Anymail backend |
| `GRADEFLOW_EMAIL_FILE_PATH` | | For the file backend |
| `GRADEFLOW_ANYMAIL_*` | | Anymail settings, such as `GRADEFLOW_ANYMAIL_MAILGUN_API_KEY` |
| `GRADEFLOW_CORS_PROXY_URL` | | The browser's route to Canvas; without it, marks can only be downloaded |
| `GRADEFLOW_MODEL_PROVIDERS` | | AI services people may connect, as JSON mapping names to OpenAI-compatible endpoints: `{"Example AI": "https://models.example.com/v1"}` |
| `GRADEFLOW_NOMAD_URL` | `http://localhost:4646` | |
| `GRADEFLOW_NOMAD_TOKEN` | | |
| `GRADEFLOW_NOMAD_DATACENTER` | `dc1` | |
| `GRADEFLOW_NOMAD_NAMESPACE` | `default` | |
| `GRADEFLOW_NOMAD_IMAGE` | `localhost:5055/gradeflow-sandbox:latest` | The app image sets the sandbox built with it |
| `GRADEFLOW_APP_URL_FOR_JOBS` | `http://localhost:8000` | Where jobs reach the app |
| `GRADEFLOW_JOB_TIME_LIMIT` | `600` | Seconds a job may run, not counting time queued |
| `GRADEFLOW_MODEL_JOB_TIME_LIMIT` | `1800` | Seconds a job that asks a language model may run |
| `GRADEFLOW_CORE_VERSION` | the installed core's commit or version | |
| `GRADEFLOW_CACHE_DIR` | `$XDG_CACHE_HOME/gradeflow` or `~/.cache/gradeflow` | The core's model cache |

## Deployment

CI publishes two images for each push to `main` (tag `main`) and each `v*` tag (the version and `latest`), plus a `sha-<commit>` tag on every build:

- `ghcr.io/gradeflowhq/app`, the web app. Its default command migrates the database and serves on port 8000. It points `GRADEFLOW_NOMAD_IMAGE` at the sandbox from the same commit.
- `ghcr.io/gradeflowhq/sandbox`, which jobs run. It includes the core's language models, since jobs have no network access beyond the app and the AI services.

[`compose.yaml`](compose.yaml) runs the web app, both workers and PostgreSQL. Put the settings in a `.env` beside it, start it with `docker compose up -d`, and put a domain and HTTPS in front of port 8000 of the `web` service. Running `docker compose up -d` again updates it: it pulls the image, migrates the database, and starts the workers once the web app is up.

`.env` needs:

- `POSTGRES_PASSWORD`, letters and digits only
- `GRADEFLOW_SECRET_KEY`, a long random string
- `GRADEFLOW_ALLOWED_HOSTS`, the domain
- an email backend and its settings, such as `GRADEFLOW_EMAIL_BACKEND=anymail.backends.mailgun.EmailBackend` with `GRADEFLOW_ANYMAIL_*`, and `GRADEFLOW_FROM_EMAIL`
- `GRADEFLOW_NOMAD_URL` and `GRADEFLOW_NOMAD_TOKEN`
- `GRADEFLOW_APP_URL_FOR_JOBS`
- `GRADEFLOW_CORS_PROXY_URL`, to send marks to Canvas
- `GRADEFLOW_MODEL_PROVIDERS`, to mark with AI
- `GRADEFLOW_VERSION`, an image tag such as `0.1`, to stay on a release line instead of `latest`

The Nomad cluster must run Nomad 2.0.3 or later, which stops each job at its time limit. Its Docker driver must be able to pull the sandbox image, and its jobs must reach `GRADEFLOW_APP_URL_FOR_JOBS`. Jobs call back to `/jobs/`, which is exempt from the HTTPS redirect, so the job URL can be plain HTTP on a private network.

Give GradeFlow a Nomad namespace of its own (`GRADEFLOW_NOMAD_NAMESPACE`), and `GRADEFLOW_NOMAD_TOKEN` a policy that reaches only its jobs and their variables:

```hcl
namespace "gradeflow" {
  capabilities = ["submit-job", "read-job", "list-jobs"]
  variables {
    path "nomad/jobs/gf_*" {
      capabilities = ["write", "list", "destroy"]
    }
  }
}
```

Each person connects one of the `GRADEFLOW_MODEL_PROVIDERS` in Settings. The token stays in their browser and reaches a job only while it runs: it's in the job's Nomad variable, which only the job can read, and the app deletes it once the job ends. Neither the app nor its jobs contact any other endpoint, so the cluster's network policy can limit jobs to the app and those hosts.

## Methods

A check names one version of a method, such as `gradeflow/key-points@1`. The core's methods ship with the core, which keeps every version it has published. A course's own methods are published as numbered versions that never change. A marking keeps the versions it names until someone chooses **Update** on a check. Archiving a method hides it from the method picker; markings that use it keep working.

## Third-party code

`src/gradeflow_app/static/vendor/` holds browser libraries under their own licenses: Alpine.js, Chart.js, @hpke/core, KaTeX, markdown-toolbar-element, Monaco Editor, @noble/hashes and Papa Parse (MIT), htmx (0BSD), Simple-DataTables (LGPL-3.0), Tom Select and SheetJS Community Edition (Apache-2.0), and the Inter font (SIL Open Font License).

## License

MIT
