from collections.abc import Callable
from uuid import UUID

from django.http import Http404

from gradeflow_app.assessments.models import Assessment, AssessmentAccess
from gradeflow_app.courses.access import member_of, with_access_for
from gradeflow_app.courses.models import Role
from gradeflow_app.requests import Request, View


def assessment_access_of(request: Request, assessment_id: UUID, role: Role = Role.VIEWER) -> AssessmentAccess:
    """The user's access to the assessment, which comes from their membership of its course."""
    assessment = Assessment.objects.select_related("course").filter(pk=assessment_id).first()
    if assessment is None:
        raise Http404
    return AssessmentAccess(member_of(request, assessment.course_id, role), assessment)


def assessment_member(role: Role = Role.VIEWER) -> Callable[[View], View]:
    """Calls the view with the user's `AssessmentAccess` to the assessment in place of the URL's `assessment` ID, once
    their role is at least `role`."""
    return with_access_for("assessment", assessment_access_of, role)
