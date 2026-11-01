from collections.abc import Callable
from uuid import UUID

from django.http import Http404

from gradeflow_app.assessments.models import Assessment, Membership
from gradeflow_app.courses.access import member_of, passing
from gradeflow_app.courses.models import Role
from gradeflow_app.requests import Request, View


def membership_of(request: Request, assessment_id: UUID, role: Role = Role.VIEWER) -> Membership:
    """The user's access to the assessment, which comes from their membership of its course."""
    assessment = Assessment.objects.select_related("course").filter(pk=assessment_id).first()
    if assessment is None:
        raise Http404
    return Membership(member_of(request, assessment.course_id, role), assessment)


def member(role: Role = Role.VIEWER) -> Callable[[View], View]:
    """Calls the view with the user's `Membership` of the assessment in place of the URL's `assessment` ID, once
    their role is at least `role`."""
    return passing("assessment", membership_of, role)
