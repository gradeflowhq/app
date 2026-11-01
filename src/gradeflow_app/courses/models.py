import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

INVITATION_LIFETIME = timedelta(days=14)


class Role(models.TextChoices):
    VIEWER = "viewer", "Viewer"
    EDITOR = "editor", "Editor"
    OWNER = "owner", "Owner"

    def at_least(self) -> list[Role]:
        """This role and those above it."""
        order = list(Role)
        return order[order.index(self) :]

    def allows(self, needed: Role) -> bool:
        return self in needed.at_least()


class Course(models.Model):
    """A course's assessments, and the people who work on them."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return self.name


class Member(models.Model):
    """A person working on a course's assessments, in a role."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        constraints = (models.UniqueConstraint(fields=("course", "user"), name="one_member_per_user"),)
        ordering = ("created_at",)

    def __str__(self) -> str:
        return f"{self.user} ({self.role})"

    @property
    def can_edit(self) -> bool:
        return Role(self.role).allows(Role.EDITOR)

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER


class Invitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)
    sent_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = (models.UniqueConstraint(fields=("course", "email"), name="one_invitation_per_course_email"),)
        ordering = ("sent_at",)

    def __str__(self) -> str:
        return self.email

    @property
    def expired(self) -> bool:
        return timezone.now() > self.sent_at + INVITATION_LIFETIME
