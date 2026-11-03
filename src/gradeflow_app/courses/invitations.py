"""Inviting people to a course: the emails, rendered from `emails/<name>.txt` (subject on the first line) and sent by
the task worker, and the invitations they carry."""

from typing import Any

from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed
from django.core import signing
from django.core.mail import send_mail
from django.db import transaction
from django.dispatch import receiver
from django.http import HttpRequest
from django.tasks import task
from django.template.loader import render_to_string
from django.utils import timezone

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.models import Invitation, Member

_SALT = "gradeflow.invitation"


@task
def send_email_task(subject: str, body: str, to: str) -> None:
    send_mail(subject, body, None, [to])


def send_email(template: str, to: str, context: dict[str, Any]) -> None:
    subject, _, body = render_to_string(f"emails/{template}.txt", context).strip().partition("\n")
    send_email_task.enqueue(subject=subject.strip(), body=body.strip() + "\n", to=to)


def invitation_token(invitation: Invitation) -> str:
    return signing.dumps(str(invitation.pk), salt=_SALT)


def invitation_from_token(token: str) -> Invitation | None:
    try:
        return Invitation.objects.select_related("course").filter(pk=signing.loads(token, salt=_SALT)).first()
    except signing.BadSignature:
        return None


@transaction.atomic
def accept_invitations(user: User, email: str) -> list[Member]:
    """Turn every open invitation sent to `email` into a membership of `user`."""
    open_invitations = [
        invitation
        for invitation in Invitation.objects.select_for_update().filter(email__iexact=email, accepted_at=None)
        if not invitation.is_expired
    ]
    members = [
        Member.objects.get_or_create(course=invitation.course, user=user, defaults={"role": invitation.role})[0]
        for invitation in open_invitations
    ]
    Invitation.objects.filter(pk__in=[invitation.pk for invitation in open_invitations]).update(
        accepted_at=timezone.now()
    )
    return members


@receiver(email_confirmed)
def _accept_on_confirmation(request: HttpRequest, email_address: EmailAddress, **kwargs: Any) -> None:
    accept_invitations(email_address.user, email_address.email)
