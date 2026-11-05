import uuid
from typing import Any, ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager["User"]):
    def create_user(self, email: str, password: str | None = None, **fields: Any) -> User:
        user = self.model(email=self.normalize_email(email), **fields)
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)
    kdf_salt = models.CharField(max_length=24)
    public_key = models.CharField(max_length=44)
    wrapped_private_key = models.CharField(max_length=104)
    """Wrapped with the key derived from the password."""
    recovery_wrapped_private_key = models.CharField(max_length=104, blank=True)
    """Wrapped with the key derived from the recovery key; empty without one."""

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    objects = UserManager()

    def __str__(self) -> str:
        return self.name or self.email


class Secret(models.Model):
    """A connection's credentials, such as a Canvas token, encrypted for the user's public key."""

    class Name(models.TextChoices):
        CANVAS = "canvas"
        AI_SERVICE = "ai-service"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="secrets")
    name = models.CharField(max_length=20, choices=Name)
    encrypted_value = models.TextField()

    class Meta:
        constraints = (models.UniqueConstraint(fields=("user", "name"), name="one_secret_per_user_name"),)

    def __str__(self) -> str:
        return f"{self.user}'s {self.name}"
