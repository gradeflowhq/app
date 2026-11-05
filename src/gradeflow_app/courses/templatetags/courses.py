from urllib.parse import urlsplit

from django import template
from django.urls import Resolver404, resolve
from django.utils.html import json_script
from django.utils.safestring import SafeString, mark_safe

from gradeflow_app.courses import keys
from gradeflow_app.courses.invitations import invitation_from_token
from gradeflow_app.courses.models import Course

register = template.Library()


@register.filter
def invited_to(url: str) -> Course | None:
    """The course an open invitation at `url` is for, so signing up or in to accept it can name the course."""
    try:
        match = resolve(urlsplit(url).path)
    except Resolver404:
        return None
    invitation = invitation_from_token(match.kwargs["token"]) if match.url_name == "invitation" else None
    return invitation.course if invitation and not invitation.is_expired else None


@register.simple_tag(takes_context=True)
def pending_course_key_shares(context: template.Context, out_of_band: bool = False) -> SafeString:
    """With `out_of_band`, htmx swaps it into the page, so a change such as an invite is shared at once."""
    script = json_script(keys.pending_course_key_shares(context["request"].user), "pending-course-key-shares")
    return mark_safe(script.replace("<script ", '<script hx-swap-oob="true" ', 1)) if out_of_band else script
