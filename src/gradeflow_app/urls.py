from django.urls import URLPattern, include, path

from gradeflow_app.accounts import views as accounts
from gradeflow_app.assessments import views as assessments
from gradeflow_app.courses import views as courses
from gradeflow_app.dashboard import views as dashboard
from gradeflow_app.editor import picker
from gradeflow_app.imports import views as imports
from gradeflow_app.jobs import callbacks
from gradeflow_app.landing import views as landing
from gradeflow_app.libraries import views as libraries
from gradeflow_app.libraries.designer import views as designer
from gradeflow_app.live import views as live
from gradeflow_app.marking import views as marking
from gradeflow_app.marking import yaml
from gradeflow_app.marking.answers import views as answers
from gradeflow_app.model_service import views as model_service
from gradeflow_app.publish import views as publish
from gradeflow_app.questions import views as questions
from gradeflow_app.results import views as results
from gradeflow_app.ui import views as ui

assessment = [
    path("", dashboard.overview, name="overview"),
    path("activity/", dashboard.activity, name="activity"),
    path("settings/", assessments.assessment_settings, name="assessment_settings"),
    path("delete/", assessments.delete, name="delete_assessment"),
    path("import/", imports.import_page, name="import"),
    path("import/upload/", imports.upload, name="upload"),
    path("import/original/", imports.original, name="import_original"),
    path("import/question-document/", imports.question_document, name="question_document"),
    path("questions/", marking.questions, name="questions"),
    path("questions/list/", marking.question_list, name="question_list"),
    path("questions/<uuid:question_id>/", marking.question, name="question"),
    path("questions/<uuid:question_id>/answers/", answers.answers, name="answers"),
    path("questions/<uuid:question_id>/answer-grouping/", answers.answer_grouping, name="answer_grouping"),
    path("questions/<uuid:question_id>/adjust/", answers.adjust, name="adjust"),
    path("questions/<uuid:question_id>/settings/", questions.question_settings, name="question_settings"),
    path("questions/<uuid:question_id>/marking/", marking.marking_panel, name="marking"),
    path("questions/<uuid:question_id>/marking/start/", marking.start_marking, name="marking_start"),
    path("questions/<uuid:question_id>/methods/", marking.question_methods, name="question_methods"),
    path("questions/<uuid:question_id>/marking/remove/", marking.remove, name="marking_remove"),
    path("questions/<uuid:question_id>/marking/history/", marking.history, name="marking_history"),
    path("questions/<uuid:question_id>/marking/restore/<int:version>/", marking.restore, name="marking_restore"),
    path("questions/<uuid:question_id>/marking/example/", marking.mark_example, name="mark_example"),
    path("questions/<uuid:question_id>/marking/save-method/", marking.save_as_method, name="method_save"),
    path("drafts/<uuid:draft>/", marking.draft_view, name="draft"),
    path("drafts/<uuid:draft>/preview/", marking.preview, name="draft_preview"),
    path("drafts/<uuid:draft>/save/", marking.save, name="draft_save"),
    path("drafts/<uuid:draft>/discard/", marking.discard, name="draft_discard"),
    path("drafts/<uuid:draft>/methods/", marking.draft_methods, name="draft_methods"),
    path("drafts/<uuid:draft>/yaml/", yaml.draft_yaml, name="draft_yaml"),
    path("question-groups/new/", marking.question_group_new, name="question_group_new"),
    path("question-groups/methods/", marking.question_group_methods, name="question_group_methods"),
    path("question-groups/drafts/<uuid:draft>/", marking.question_group_draft, name="question_group_draft"),
    path(
        "question-groups/drafts/<uuid:draft>/answers/",
        marking.question_group_draft_answers,
        name="question_group_draft_answers",
    ),
    path("question-groups/<uuid:marking_id>/", marking.question_group, name="question_group"),
    path("question-groups/<uuid:marking_id>/answers/", marking.question_group_answers, name="question_group_answers"),
    path("question-groups/<uuid:marking_id>/edit/", marking.question_group_edit, name="question_group_edit"),
    path("question-groups/<uuid:marking_id>/remove/", marking.question_group_remove, name="question_group_remove"),
    path(
        "question-groups/<uuid:marking_id>/save-method/",
        marking.question_group_save_as_method,
        name="question_group_method_save",
    ),
    path("students/", results.students, name="students"),
    path("students/<uuid:student>/", results.student, name="student"),
    path("download/", results.download, name="download"),
    path("publish/", publish.publish, name="publish"),
    path("publish/rows/", publish.rows, name="publish_rows"),
    path("publish/comment/", publish.comment, name="publish_comment"),
    path("publish/settings/", publish.save_sending, name="publish_settings"),
    path("publish/sent/", publish.sent, name="publish_sent"),
]


def workspace(prefix: str) -> list[URLPattern]:
    """A method's workspace: a person's draft of it, or with a version `number`, a published version."""
    return [
        path(prefix, designer.page, name="method_page"),
        path(f"{prefix}answers/", designer.answers, name="method_answers"),
        path(f"{prefix}values/", designer.values, name="method_values"),
        path(f"{prefix}run/", designer.run, name="method_run"),
        path(f"{prefix}results/", designer.results, name="method_results"),
        path(f"{prefix}check/", designer.check, name="method_check"),
        path(f"{prefix}inputs/", designer.inputs, name="method_inputs"),
        path(f"{prefix}question/", designer.question, name="method_question"),
        path(f"{prefix}details/", designer.details, name="method_details"),
        path(f"{prefix}yaml/", designer.yaml, name="method_yaml"),
    ]


course = [
    path("", courses.course, name="course"),
    path("members/", courses.members, name="members"),
    path("members/<uuid:user>/role/", courses.member_role, name="member_role"),
    path("members/<uuid:user>/remove/", courses.member_remove, name="member_remove"),
    path("leave/", courses.leave_course, name="leave_course"),
    path("canvas/", publish.canvas, name="canvas"),
    path("methods/", libraries.course_methods, name="course_methods"),
    path("invitations/<uuid:invitation_id>/revoke/", courses.invitation_revoke, name="invitation_revoke"),
    path("invitations/<uuid:invitation_id>/resend/", courses.invitation_resend, name="invitation_resend"),
    path("invitations/<uuid:invitation_id>/role/", courses.invitation_role, name="invitation_role"),
    path("settings/", courses.course_settings, name="course_settings"),
    path("delete/", courses.delete_course, name="delete_course"),
]

method = [
    path("", designer.published, name="published"),
    path("edit/", designer.edit, name="method_edit"),
    path("archive/", libraries.archive, name="method_archive"),
    path("share/", libraries.share, name="method_share"),
    path("copy/", libraries.copy, name="method_copy"),
    path("delete/", libraries.delete, name="method_delete"),
    path("draft/methods/", designer.methods_to_choose, name="method_picker"),
    path("draft/field/", designer.field, name="method_field"),
    path("draft/publish/", designer.publish_draft, name="method_publish"),
    path("draft/discard/", designer.discard, name="method_discard"),
    *workspace("draft/"),
    *workspace("v<int:number>/"),
]

urlpatterns = [
    path("", landing.landing, name="landing"),
    path("accounts/", include("allauth.urls")),
    path("settings/", accounts.account_settings, name="settings"),
    path("settings/devices/<int:session>/sign-out/", accounts.sign_out_device, name="sign_out_device"),
    path("settings/integrations/", accounts.integrations, name="integrations"),
    path("settings/model-service/", model_service.models, name="model_service"),
    path("settings/model-service/connect/", model_service.connect, name="model_service_connect"),
    path("settings/model-service/chooser/", model_service.chooser, name="model_service_chooser"),
    path("settings/model-service/choose/", model_service.choose, name="model_service_choose"),
    path(
        "settings/model-service/answers-per-request/",
        model_service.answers_per_request,
        name="model_service_answers_per_request",
    ),
    path("settings/model-service/stop/", model_service.stop, name="model_service_stop"),
    path("settings/model-service/test/", model_service.test, name="model_service_test"),
    path("settings/model-service/status/", model_service.status, name="model_service_status"),
    path("courses/", courses.courses, name="courses"),
    path("courses/<uuid:course>/", include(course)),
    path("methods/", libraries.your_methods, name="your_methods"),
    path("methods/new/", libraries.new, name="method_new"),
    path("methods/guide/<path:ref>/", picker.method, name="method_guide"),
    path("methods/<uuid:method>/", include(method)),
    path("assessments/<uuid:assessment>/", include(assessment)),
    path("invitations/<str:value>/", courses.invitation, name="invitation"),
    path("jobs/<uuid:job_id>/input", callbacks.job_input, name="job_input"),
    path("jobs/<uuid:job_id>/callback", callbacks.job_callback, name="job_callback"),
    path("live/", live.stream, name="live"),
    path("markdown/", ui.markdown_preview, name="markdown_preview"),
]
