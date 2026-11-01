from urllib.parse import urlsplit

from django import template
from django.urls import Resolver404, resolve

from gradeflow_app.courses.invitations import invitation_of
from gradeflow_app.courses.models import Course

register = template.Library()


@register.filter
def invited_to(url: str) -> Course | None:
    """The course a live invitation at `url` is for, so signing up or in to accept it can name the course."""
    try:
        match = resolve(urlsplit(url).path)
    except Resolver404:
        return None
    invitation = invitation_of(match.kwargs["value"]) if match.url_name == "invitation" else None
    return invitation.course if invitation and not invitation.expired else None
