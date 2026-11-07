// The script on every page: the page-wide behaviours, then Alpine with every component.

import Alpine from "../vendor/alpinejs-csp-3.17.4/module.esm.min.js";
import * as allauth from "./behaviours/allauth.js";
import * as busyButtons from "./behaviours/busy-buttons.js";
import * as courseKeySharing from "./behaviours/course-key-sharing.js";
import * as dialogButtons from "./behaviours/dialog-buttons.js";
import * as downloads from "./behaviours/downloads.js";
import * as forms from "./behaviours/forms.js";
import * as htmxSetup from "./behaviours/htmx.js";
import * as messages from "./behaviours/messages.js";
import * as methodRevision from "./behaviours/method-revision.js";
import * as pageLoading from "./behaviours/page-loading.js";
import * as passwordForms from "./behaviours/password-forms.js";
import * as rowLinks from "./behaviours/row-links.js";
import * as signOut from "./behaviours/sign-out.js";
import * as studentIds from "./behaviours/student-ids.js";
import * as timezone from "./behaviours/timezone.js";
import { adjustForm } from "./components/adjust-form.js";
import { canvasConnection } from "./components/canvas/connection.js";
import { canvasCourse } from "./components/canvas/course.js";
import { canvasExport } from "./components/canvas/export.js";
import { codeEditor } from "./components/code-editor.js";
import { collapsible } from "./components/collapsible.js";
import { fileDrop } from "./components/file-drop.js";
import { histogram } from "./components/histogram.js";
import { importer } from "./components/import/importer.js";
import { answerGroup, canvasSend, markingExamples } from "./components/landing-demos.js";
import { markdownEditor } from "./components/markdown-editor.js";
import { math } from "./components/math.js";
import { modelServiceCard, modelServiceNotice } from "./components/model-service-card.js";
import { openOnLoad } from "./components/open-on-load.js";
import { orderedChoices } from "./components/ordered-choices.js";
import { recoveryKeyCard } from "./components/recovery-key-card.js";
import { choiceList, methodPicker, searchList } from "./components/search-list.js";
import { studentTable } from "./components/student-table.js";
import { tagInput } from "./components/tag-input.js";
import { testAnswer } from "./components/test-answer.js";
import { themeChoice } from "./components/theme-choice.js";
import { toastStore } from "./services/toasts.js";

Alpine.store("toasts", toastStore);

// In the order their listeners run.
for (const behaviour of [
  timezone,
  messages,
  signOut,
  courseKeySharing,
  passwordForms,
  downloads,
  dialogButtons,
  studentIds,
  methodRevision,
  htmxSetup,
  pageLoading,
  busyButtons,
  forms,
  rowLinks,
  allauth,
]) {
  behaviour.start();
}

const components = {
  adjustForm,
  answerGroup,
  canvasConnection,
  canvasCourse,
  canvasExport,
  canvasSend,
  choiceList,
  codeEditor,
  collapsible,
  fileDrop,
  histogram,
  importer,
  markdownEditor,
  markingExamples,
  math,
  methodPicker,
  modelServiceCard,
  modelServiceNotice,
  openOnLoad,
  orderedChoices,
  recoveryKeyCard,
  searchList,
  studentTable,
  tagInput,
  testAnswer,
  themeChoice,
};
for (const [name, component] of Object.entries(components)) Alpine.data(name, component);
window.Alpine = Alpine; // for htmx's alpine-compat extension
Alpine.start();
