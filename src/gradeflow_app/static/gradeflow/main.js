// The script on every page: the page-wide behaviours, then Alpine with the components.

import Alpine from "../vendor/alpinejs-csp-3.17.4/module.esm.min.js";
import * as allauth from "./behaviours/allauth.js";
import * as busyButtons from "./behaviours/busy-buttons.js";
import * as courseKeySharing from "./behaviours/course-key-sharing.js";
import * as currentTab from "./behaviours/current-tab.js";
import * as dialogButtons from "./behaviours/dialog-buttons.js";
import * as downloads from "./behaviours/downloads.js";
import * as forms from "./behaviours/forms.js";
import * as htmxSetup from "./behaviours/htmx.js";
import * as messages from "./behaviours/messages.js";
import * as methodRevision from "./behaviours/method-revision.js";
import * as pageLoading from "./behaviours/page-loading.js";
import * as passwordForms from "./behaviours/password-forms.js";
import * as rowLinks from "./behaviours/row-links.js";
import * as scrollLock from "./behaviours/scroll-lock.js";
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
import { markingInstructions, markingInstructionsToggle } from "./components/marking-instructions.js";
import { markdownEditor } from "./components/markdown-editor.js";
import { markingResizer } from "./components/marking-resizer.js";
import { math } from "./components/math.js";
import { modelServiceNotice, modelServicePanel } from "./components/model-service-panel.js";
import { openOnLoad } from "./components/open-on-load.js";
import { orderedChoices } from "./components/ordered-choices.js";
import { questionsCollapse } from "./components/questions-collapse.js";
import { questionsToggle } from "./components/questions-toggle.js";
import { recoveryKey } from "./components/recovery-key.js";
import { choiceList, methodPicker, searchList } from "./components/search-list.js";
import { studentsGrid } from "./components/students-grid.js";
import { tagInput } from "./components/tag-input.js";
import { testAnswer } from "./components/test-answer.js";
import { themeChoice } from "./components/theme-choice.js";
import { workspaceSwitch } from "./components/workspace-switch.js";
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
  scrollLock,
  studentIds,
  methodRevision,
  htmxSetup,
  pageLoading,
  busyButtons,
  forms,
  rowLinks,
  currentTab,
  allauth,
]) {
  behaviour.start();
}

const components = {
  adjustForm,
  canvasConnection,
  canvasCourse,
  canvasExport,
  choiceList,
  codeEditor,
  collapsible,
  fileDrop,
  histogram,
  importer,
  markdownEditor,
  markingInstructions,
  markingInstructionsToggle,
  markingResizer,
  math,
  methodPicker,
  modelServiceNotice,
  modelServicePanel,
  openOnLoad,
  orderedChoices,
  questionsCollapse,
  questionsToggle,
  recoveryKey,
  searchList,
  studentsGrid,
  tagInput,
  testAnswer,
  themeChoice,
  workspaceSwitch,
};
for (const [name, component] of Object.entries(components)) Alpine.data(name, component);
window.Alpine = Alpine; // for htmx's alpine-compat extension
Alpine.start();
