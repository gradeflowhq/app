"""Libraries made with the people and courses that own them, and namespaces that follow their email address."""

from typing import Any

from allauth.account.models import EmailAddress
from allauth.account.signals import email_changed
from django.db.models.signals import post_save
from django.dispatch import receiver

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.models import Course
from gradeflow_app.libraries.models import Library
from gradeflow_app.libraries.services import available_namespace, rename_namespace


@receiver(post_save, sender=User)
def _create_user_library(instance: User, created: bool, raw: bool, **kwargs: Any) -> None:
    if created and not raw:
        Library.objects.create(namespace=available_namespace(instance.email), user=instance)


@receiver(post_save, sender=Course)
def _create_course_library(instance: Course, created: bool, raw: bool, **kwargs: Any) -> None:
    if created and not raw:
        Library.objects.create(namespace=available_namespace(instance.name), course=instance)


@receiver(email_changed)
def _rename_user_namespace(user: User, to_email_address: EmailAddress, **kwargs: Any) -> None:
    rename_namespace(user.library, to_email_address.email)
