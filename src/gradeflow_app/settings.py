"""Settings, read from `GRADEFLOW_*` environment variables."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import dj_database_url
from django.urls import reverse_lazy
from django.utils.csp import CSP

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = os.environ.get("GRADEFLOW_SECRET_KEY", "")
DEBUG = os.environ.get("GRADEFLOW_DEBUG") == "true"
ALLOWED_HOSTS = os.environ.get("GRADEFLOW_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.forms",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "allauth.usersessions",
    "django_cotton",
    "django_htmx",
    "django_tailwind_cli",
    "django_tasks_db",
    "heroicons",
    "simple_history",
    "gradeflow_app.ui",
    "gradeflow_app.accounts",
    "gradeflow_app.model_service",
    "gradeflow_app.courses",
    "gradeflow_app.assessments",
    "gradeflow_app.dashboard",
    "gradeflow_app.imports",
    "gradeflow_app.questions",
    "gradeflow_app.libraries",
    "gradeflow_app.jobs",
    "gradeflow_app.marking",
    "gradeflow_app.results",
    "gradeflow_app.publish",
    "gradeflow_app.live",
    "gradeflow_app.landing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "allauth.usersessions.middleware.UserSessionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "gradeflow_app.ui.middleware.HtmxMessagesMiddleware",
    "gradeflow_app.ui.middleware.TimezoneMiddleware",
]

ROOT_URLCONF = "gradeflow_app.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.csp",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
            "builtins": ["gradeflow_app.ui.templatetags.ui"],
        },
    },
]
FORM_RENDERER = "gradeflow_app.ui.forms.Renderer"

DATABASES = {
    "default": dj_database_url.config(
        "GRADEFLOW_DATABASE_URL", default="postgres://gradeflow:gradeflow@localhost:5434/gradeflow"
    )
}
if test_database := os.environ.get("GRADEFLOW_TEST_DATABASE"):
    DATABASES["default"]["TEST"] = {"NAME": test_database}
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "cache",
        "OPTIONS": {"MAX_ENTRIES": 100_000},
    }
}
"""Keeps prepared answer comparisons across questions and threshold changes."""
TASKS = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "courses"

ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*"]
ACCOUNT_ADAPTER = "gradeflow_app.accounts.adapter.AccountAdapter"
ACCOUNT_SIGNUP_FORM_CLASS = "gradeflow_app.accounts.forms.SignupForm"
ACCOUNT_FORMS = {
    name: f"gradeflow_app.accounts.allauth_forms.{form}"
    for name, form in {
        "login": "LoginForm",
        "signup": "SignupForm",
        "add_email": "AddEmailForm",
        "change_password": "ChangePasswordForm",
        "set_password": "SetPasswordForm",
        "reset_password": "ResetPasswordForm",
        "reset_password_from_key": "ResetPasswordKeyForm",
        "request_login_code": "RequestLoginCodeForm",
        "confirm_login_code": "ConfirmLoginCodeForm",
        "confirm_email_verification_code": "ConfirmEmailVerificationCodeForm",
        "confirm_password_reset_code": "ConfirmPasswordResetCodeForm",
        "reauthenticate": "ReauthenticateForm",
    }.items()
}
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_PASSWORD_RESET_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_EMAIL_CONFIRMATION_AUTHENTICATED_REDIRECT_URL = reverse_lazy("account_email")
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_EMAIL_SUBJECT_PREFIX = ""
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes", "webauthn"]
MFA_PASSKEY_LOGIN_ENABLED = True
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = DEBUG
USERSESSIONS_TRACK_ACTIVITY = True

DEFAULT_FROM_EMAIL = os.environ.get("GRADEFLOW_FROM_EMAIL", "GradeFlow <noreply@localhost>")
MAILERS = {
    "default": {
        "BACKEND": os.environ.get("GRADEFLOW_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"),
        "OPTIONS": {"file_path": path} if (path := os.environ.get("GRADEFLOW_EMAIL_FILE_PATH")) else {},
    }
}
ANYMAIL = {
    name.removeprefix("GRADEFLOW_ANYMAIL_"): value
    for name, value in os.environ.items()
    if name.startswith("GRADEFLOW_ANYMAIL_")
}

USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en"

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = os.environ.get("GRADEFLOW_STATIC_ROOT") or None
if STATIC_ROOT:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    }

TAILWIND_CLI_USE_DAISY_UI = True
TAILWIND_CLI_VERSION = "2.10.34"
TAILWIND_CLI_SRC_CSS = BASE_DIR / "ui" / "styles.css"
TAILWIND_CLI_DIST_CSS = "css/gradeflow.css"

GRADEFLOW_CORS_PROXY_URL = os.environ.get("GRADEFLOW_CORS_PROXY_URL", "")
"""Where the browser sends its Canvas requests; without one, marks can only be downloaded."""
_PROXY = urlsplit(GRADEFLOW_CORS_PROXY_URL)

SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, CSP.NONCE],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
    "img-src": [CSP.SELF, "data:", "blob:"],
    "font-src": [CSP.SELF, "data:"],
    "worker-src": [CSP.SELF, "blob:"],
    "connect-src": [CSP.SELF, *([f"{_PROXY.scheme}://{_PROXY.netloc}"] if _PROXY.netloc else [])],
    "frame-ancestors": [CSP.NONE],
    "form-action": [CSP.SELF],
}
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_REDIRECT_EXEMPT = [r"^jobs/"]
    """Sandbox jobs call back over the cluster's network, not through the proxy."""
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"console": {"class": "logging.StreamHandler"}},
        "root": {"handlers": ["console"], "level": os.environ.get("GRADEFLOW_LOG_LEVEL", "INFO")},
    }

DATA_UPLOAD_MAX_MEMORY_SIZE = int(2.5 * 2**20)
"""The largest request body, files apart; sandbox jobs send their results in batches of 1 MB to stay under it."""

GRADEFLOW_NOMAD = {
    "url": os.environ.get("GRADEFLOW_NOMAD_URL", "http://localhost:4646"),
    "token": os.environ.get("GRADEFLOW_NOMAD_TOKEN", ""),
    "image": os.environ.get("GRADEFLOW_NOMAD_IMAGE", "localhost:5055/gradeflow-sandbox:latest"),
    "datacenter": os.environ.get("GRADEFLOW_NOMAD_DATACENTER", "dc1"),
    "namespace": os.environ.get("GRADEFLOW_NOMAD_NAMESPACE", "default"),
}
GRADEFLOW_JOB_URL = os.environ.get("GRADEFLOW_JOB_URL", "http://localhost:8000")
"""Where sandbox jobs reach the app for their input and callbacks."""
GRADEFLOW_JOB_TIME_LIMIT = int(os.environ.get("GRADEFLOW_JOB_TIME_LIMIT", "600"))
"""Seconds a job may run in the sandbox."""
GRADEFLOW_MODEL_JOB_TIME_LIMIT = int(os.environ.get("GRADEFLOW_MODEL_JOB_TIME_LIMIT", "1800"))
"""For jobs that ask a language model, whose reasoning takes minutes."""
GRADEFLOW_MODEL_PROVIDERS: dict[str, str] = json.loads(os.environ.get("GRADEFLOW_MODEL_PROVIDERS", "{}"))
"""The AI services people may connect, each name with its OpenAI-compatible endpoint. No request goes to any other."""
GRADEFLOW_JOBS_PER_USER = int(os.environ.get("GRADEFLOW_JOBS_PER_USER", "5"))
"""Most jobs a user may have running at once."""
