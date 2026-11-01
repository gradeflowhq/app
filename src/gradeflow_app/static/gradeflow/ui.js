// Behaviour shared by every page, and Alpine with every component registered.

import Alpine from "../vendor/alpinejs-csp-3.17.4/module.esm.min.js";
import { canvasConnection, canvasCourse, canvasPublisher } from "./canvas.js";
import { histogram } from "./charts.js";
import { codeEditor } from "./code.js";
import "./downloads.js";
import { fileDrop } from "./drop.js";
import { importer } from "./importer.js";
import { answerGroup, answerGroups, canvasSend, markingExamples, studentIds } from "./landing.js";
import { markdownEditor } from "./markdown.js";
import { math } from "./math.js";
import { modelService } from "./models.js";
import { orderedChoices } from "./ordered.js";
import { choiceList, methodPicker } from "./picker.js";
import { passphraseSettings } from "./passphrase.js";
import { adjustment } from "./student.js";
import { studentTable } from "./table.js";
import { tags } from "./tags.js";
import { themeChoice } from "./theme.js";
// Listeners on every page, in the order they run.
import { testAnswer } from "./methods.js";
import "./htmx.js";
import { shownDialog } from "./dialogs.js";
import "./fields.js";

// allauth starts its passkey forms on DOMContentLoaded, which a page that htmx swaps in never fires, so they start
// here once their scripts have loaded. A full page load leaves them to allauth.
const ALLAUTH_FORMS = "script[data-allauth-onload]:not([data-started])";
function startAllauth() {
  for (const script of document.querySelectorAll(ALLAUTH_FORMS)) {
    const start = script.dataset.allauthOnload.split(".").reduce((found, part) => found?.[part], window);
    if (typeof start !== "function") continue;
    script.dataset.started = "";
    start(JSON.parse(script.textContent));
  }
}
document.querySelectorAll(ALLAUTH_FORMS).forEach((script) => (script.dataset.started = ""));
document.addEventListener("htmx:after:settle", () => {
  startAllauth();
  document.querySelectorAll("script[src]").forEach((script) => script.addEventListener("load", startAllauth, { once: true }));
});

const components = {
  adjustment,
  answerGroup,
  answerGroups,
  canvasConnection,
  canvasCourse,
  canvasPublisher,
  canvasSend,
  choiceList,
  codeEditor,
  fileDrop,
  histogram,
  importer,
  markdownEditor,
  markingExamples,
  math,
  methodPicker,
  modelService,
  orderedChoices,
  passphraseSettings,
  shownDialog,
  studentIds,
  studentTable,
  tags,
  testAnswer,
  themeChoice,
};
for (const [name, component] of Object.entries(components)) Alpine.data(name, component);
window.Alpine = Alpine; // for htmx's alpine-compat extension
Alpine.start();