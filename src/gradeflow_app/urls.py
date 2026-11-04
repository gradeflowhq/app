from django.urls import URLPattern, URLResolver, include, path

from gradeflow_app.accounts import views as accounts
from gradeflow_app.assessments import views as assessments
from gradeflow_app.courses import views as courses
from gradeflow_app.dashboard import views as dashboard
from gradeflow_app.editor import picker
from gradeflow_app.exports import views as exports
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
from gradeflow_app.questions import views as questions
from gradeflow_app.results import views as results
from gradeflow_app.ui import views as ui

assessment_urls = [
    path("", dashboard.overview, name="overview"),
    path("activity/", dashboard.activity, name="activity"),
    path("settings/", assessments.assessment_settings, name="assessment_settings"),
    path("delete/", assessments.delete_assessment, name="delete_assessment"),
    path("import/", imports.import_page, name="import"),
    path("import/upload/", imports.upload, name="import_upload"),
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
    path("questions/<uuid:question_id>/marking/restore/<int:history_id>/", marking.restore, name="marking_restore"),
    path("questions/<uuid:question_id>/marking/example/", marking.mark_example, name="mark_example"),
    path("questions/<uuid:question_id>/marking/save-method/", marking.save_as_method, name="method_save"),
    path("drafts/<uuid:draft_id>/", marking.draft_view, name="draft"),
    path("drafts/<uuid:draft_id>/preview/", marking.preview, name="draft_preview"),
    path("drafts/<uuid:draft_id>/save/", marking.save, name="draft_save"),
    path("drafts/<uuid:draft_id>/discard/", marking.discard, name="draft_discard"),
    path("drafts/<uuid:draft_id>/methods/", marking.draft_methods, name="draft_methods"),
    path("drafts/<uuid:draft_id>/yaml/", yaml.draft_yaml, name="draft_yaml"),
    path("question-groups/new/", marking.question_group_new, name="question_group_new"),
    path("question-groups/methods/", marking.question_group_methods, name="question_group_methods"),
    path("question-groups/drafts/<uuid:draft_id>/", marking.question_group_draft, name="question_group_draft"),
    path(
        "question-groups/drafts/<uuid:draft_id>/answers/",
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
    path("students/<uuid:student_id>/", results.student, name="student"),
    path("download/", results.download, name="download"),
    path("export/", exports.send_marks, name="export"),
    path("export/rows/", exports.canvas_rows, name="export_rows"),
    path("export/comment/", exports.comment, name="export_comment"),
    path("export/settings/", exports.save_sending, name="export_sending"),
    path("export/sent/", exports.record_sent, name="export_record"),
]


method_workspace_urls: list[URLPattern] = [
    path("", designer.page, name="method_page"),
    path("answers/", designer.answers, name="method_answers"),
    path("values/", designer.values, name="method_values"),
    path("run/", designer.run, name="method_run"),
    path("results/", designer.results, name="method_results"),
    path("check/", designer.check, name="method_check"),
    path("inputs/", designer.inputs, name="method_inputs"),
    path("question/", designer.question, name="method_question"),
    path("details/", designer.details, name="method_details"),
    path("yaml/", designer.yaml, name="method_yaml"),
]
"""A method's workspace: a person's draft of it, or with a version `number`, a published version."""

course_urls = [
    path("", courses.course, name="course"),
    path("members/", courses.members, name="members"),
    path("members/<uuid:user_id>/role/", courses.member_role, name="member_role"),
    path("members/<uuid:user_id>/remove/", courses.member_remove, name="member_remove"),
    path("leave/", courses.leave_course, name="leave_course"),
    path("canvas/", exports.canvas_settings, name="canvas_settings"),
    path("methods/", libraries.course_methods, name="course_methods"),
    path("invitations/<uuid:invitation_id>/revoke/", courses.invitation_revoke, name="invitation_revoke"),
    path("invitations/<uuid:invitation_id>/resend/", courses.invitation_resend, name="invitation_resend"),
    path("invitations/<uuid:invitation_id>/role/", courses.invitation_role, name="invitation_role"),
    path("settings/", courses.course_settings, name="course_settings"),
    path("delete/", courses.delete_course, name="delete_course"),
]

method_urls: list[URLPattern | URLResolver] = [
    path("", designer.latest_version, name="method_latest"),
    path("edit/", designer.start_editing, name="method_edit"),
    path("archive/", libraries.archive, name="method_archive"),
    path("share/", libraries.set_public, name="method_share"),
    path("copy/", libraries.copy_method, name="method_copy"),
    path("delete/", libraries.delete, name="method_delete"),
    path("draft/methods/", designer.method_choices, name="method_choices"),
    path("draft/field/", designer.fill_setting, name="method_field"),
    path("draft/publish/", designer.publish_draft, name="method_publish"),
    path("draft/discard/", designer.discard, name="method_discard"),
    path("draft/", include(method_workspace_urls)),
    path("v<int:number>/", include(method_workspace_urls)),
]

urlpatterns = [
    path("", landing.landing, name="landing"),
    path("accounts/", include("allauth.urls")),
    path("settings/", accounts.account_settings, name="settings"),
    path("settings/devices/<int:session_id>/sign-out/", accounts.sign_out_device, name="sign_out_device"),
    path("settings/integrations/", accounts.integrations, name="integrations"),
    path("settings/model-service/", model_service.models, name="model_service"),
    path("settings/model-service/connect/", model_service.connect, name="model_service_connect"),
    path("settings/model-service/models/", model_service.models_dialog, name="model_service_dialog"),
    path("settings/model-service/table/", model_service.table, name="model_service_table"),
    path("settings/model-service/choose/", model_service.choose, name="model_service_choose"),
    path("settings/model-service/automatic/", model_service.automatic, name="model_service_automatic"),
    path("settings/model-service/use/", model_service.use, name="model_service_use"),
    path(
        "settings/model-service/answers-per-request/",
        model_service.answers_per_request,
        name="model_service_answers_per_request",
    ),
    path("settings/model-service/stop/", model_service.stop, name="model_service_stop"),
    path("settings/model-service/test/", model_service.test, name="model_service_test"),
    path("courses/", courses.courses, name="courses"),
    path("courses/<uuid:course>/", include(course_urls)),
    path("methods/", libraries.your_methods, name="your_methods"),
    path("methods/new/", libraries.new_method, name="method_new"),
    path("methods/guide/<path:ref>/", picker.method_guide, name="method_guide"),
    path("methods/<uuid:method>/", include(method_urls)),
    path("assessments/<uuid:assessment>/", include(assessment_urls)),
    path("invitations/<str:token>/", courses.invitation, name="invitation"),
    path("jobs/<str:run_id>/input", callbacks.job_input, name="job_input"),
    path("jobs/<str:run_id>/callback", callbacks.job_callback, name="job_callback"),
    path("live/", live.stream, name="live"),
    path("markdown/", ui.markdown_preview, name="markdown_preview"),
]
