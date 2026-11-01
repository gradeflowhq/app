// Each assessment's student ID passphrase, kept in this browser only: in localStorage when remembered,
// otherwise for the tab in sessionStorage. The passphrase dialog (c-passphrase) asks for it when needed.

import { decryptId } from "./crypto.js";
import { local, session } from "./storage.js";
import { report } from "./toasts.js";

const key = (assessment) => `gradeflow:passphrase:${assessment}`;

export const stored = (assessment) => local.get(key(assessment)) ?? session.get(key(assessment));

export function remember(assessment, passphrase, persist) {
  forget(assessment);
  (persist ? local : session).set(key(assessment), passphrase);
}

function forget(assessment) {
  local.remove(key(assessment));
  session.remove(key(assessment));
}

// The assessment settings' passphrase row: whether this device knows it, entering it, and forgetting it.
export const passphraseSettings = () => ({
  known: false,
  get assessment() {
    return this.$root.dataset.assessmentId;
  },
  init() {
    this.known = Boolean(stored(this.assessment));
  },
  get status() {
    if (!this.known) return "Not remembered on this device";
    return remembered(this.assessment) ? "Remembered on this device" : "Remembered until this tab closes";
  },
  async enter() {
    this.known = Boolean(await passphraseFor(this.assessment, this.$root.dataset.sample));
  },
  forget() {
    forget(this.assessment);
    this.known = false;
    document.dispatchEvent(new Event("gradeflow:locked"));
    report("Forgotten on this device.", "success");
  },
});

export const remembered = (assessment) => local.get(key(assessment)) !== null;

export const opens = (passphrase, envelope) =>
  decryptId(envelope, passphrase).then(
    () => true,
    () => false,
  );

const asking = new Map();

/** The assessment's passphrase this browser keeps, if it opens `envelope`. Never asks. */
export async function known(assessment, envelope) {
  const kept = stored(assessment);
  return kept && (await opens(kept, envelope)) ? kept : null;
}

/** The assessment's passphrase, checked against `envelope`; asks for it if needed. Null if the person cancels. */
export async function passphraseFor(assessment, envelope) {
  const kept = await known(assessment, envelope);
  if (kept) return kept;
  if (!asking.has(assessment)) asking.set(assessment, ask(assessment, envelope).finally(() => asking.delete(assessment)));
  return asking.get(assessment);
}

function ask(assessment, envelope) {
  const dialog = document.getElementById("passphrase");
  if (!dialog) return Promise.resolve(null);
  const form = dialog.querySelector("form[data-passphrase]");
  const error = dialog.querySelector("[data-error]");
  error.hidden = true;
  form.reset();
  const asked = new AbortController();
  return new Promise((resolve) => {
    // Captured on the dialog so the form's own listeners, such as htmx boosting, never send the passphrase anywhere.
    dialog.addEventListener(
      "submit",
      async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const passphrase = form.elements.passphrase.value;
        if (!(await opens(passphrase, envelope))) {
          error.hidden = false;
          form.elements.passphrase.select();
          return;
        }
        remember(assessment, passphrase, form.elements.remember.checked);
        document.dispatchEvent(new Event("gradeflow:unlocked"));
        dialog.close("unlocked");
      },
      { capture: true, signal: asked.signal },
    );
    dialog.addEventListener(
      "close",
      () => {
        asked.abort();
        resolve(dialog.returnValue === "unlocked" ? form.elements.passphrase.value : null);
      },
      { signal: asked.signal },
    );
    dialog.returnValue = "";
    dialog.showModal();
    form.elements.passphrase.focus();
  });
}
