import re

from django.core.exceptions import ValidationError

from gradeflow_app.accounts.models import User

AUTH_KEY = re.compile(r"[0-9a-f]{64}")


class AuthKeyValidator:
    """Accepts only an authentication key (lib/account-crypto.js), never a password."""

    def validate(self, password: str, user: User | None = None) -> None:
        if not AUTH_KEY.fullmatch(password):
            raise ValidationError("GradeFlow needs JavaScript to set a password.", code="not_an_auth_key")

    def get_help_text(self) -> str:
        return ""
