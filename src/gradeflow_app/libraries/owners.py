"""Libraries made with the people who own them, and namespaces that follow their email address."""

from typing import Any

from allauth.account.models import EmailAddress
from allauth.account.signals import email_changed
from django.db.models.signals import post_save
from django.dispatch import receiver

from gradeflow_app.accounts.models import User
from gradeflow_app.libraries.models import Library
from gradeflow_app.libraries.services import follow, free_namespace


@receiver(post_save, sender=User)
def _signed_up(instance: User, created: bool, raw: bool, **kwargs: Any) -> None:
    if created and not raw:
        Library.objects.create(namespace=free_namespace(instance.email), user=instance)


@receiver(email_changed)
def _email_changed(user: User, to_email_address: EmailAddress, **kwargs: Any) -> None:
    follow(user.library, to_email_address.email)
