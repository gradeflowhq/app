import re

from django.core.exceptions import ValidationError

from gradeflow_app.accounts.models import User

AUTH_KEY = re.compile(r"[0-9a-f]{64}")


class AuthKeyValidator:
    """Accepts only an authentication key derived in the browser (account-crypto.js), so no password is stored."""

    def validate(self, password: str, user: User | None = None) -> None:
        if not AUTH_KEY.fullmatch(password):
            raise ValidationError("GradeFlow needs JavaScript to set a password.", code="not_a_login_key")

    def get_help_text(self) -> str:
        return ""
