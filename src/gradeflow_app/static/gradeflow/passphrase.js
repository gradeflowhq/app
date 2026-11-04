// Each assessment's student ID passphrase, kept in this browser only: in localStorage when remembered,
// otherwise for the tab in sessionStorage. The passphrase dialog (c-passphrase) asks for it when needed.

import { decryptId } from "./crypto.js";
import { showToast } from "./toasts.js";

const key = (assessment) => `gradeflow:passphrase:${assessment}`;

export const storedPassphrase = (assessment) => localStorage.getItem(key(assessment)) ?? sessionStorage.getItem(key(assessment));

export function remember(assessment, passphrase, persist) {
  forget(assessment);
  (persist ? localStorage : sessionStorage).setItem(key(assessment), passphrase);
}

function forget(assessment) {
  localStorage.removeItem(key(assessment));
  sessionStorage.removeItem(key(assessment));
}

// The assessment settings' passphrase row: whether this device knows it, entering it, and forgetting it.
export const passphraseSettings = () => ({
  isKnown: false,
  get assessment() {
    return this.$root.dataset.assessmentId;
  },
  init() {
    this.isKnown = Boolean(storedPassphrase(this.assessment));
  },
  get status() {
    if (!this.isKnown) return "Not remembered on this device";
    return isRemembered(this.assessment) ? "Remembered on this device" : "Remembered until this tab closes";
  },
  async enter() {
    this.isKnown = Boolean(await passphraseFor(this.assessment, this.$root.dataset.sampleEncryptedId));
  },
  forget() {
    forget(this.assessment);
    this.isKnown = false;
    document.dispatchEvent(new Event("gradeflow:passphrase-forgotten"));
    showToast("Forgotten on this device.", "success");
  },
});

export const isRemembered = (assessment) => localStorage.getItem(key(assessment)) !== null;

export const passphraseOpens = (passphrase, encryptedId) =>
  decryptId(encryptedId, passphrase).then(
    () => true,
    () => false,
  );

const asking = new Map();

/** The assessment's passphrase this browser keeps, if it opens `encryptedId`. Never asks. */
export async function knownPassphrase(assessment, encryptedId) {
  const passphrase = storedPassphrase(assessment);
  return passphrase && (await passphraseOpens(passphrase, encryptedId)) ? passphrase : null;
}

/** The assessment's passphrase, checked against `encryptedId`; asks for it if needed. Null if the person cancels. */
export async function passphraseFor(assessment, encryptedId) {
  const passphrase = await knownPassphrase(assessment, encryptedId);
  if (passphrase) return passphrase;
  if (!asking.has(assessment)) asking.set(assessment, ask(assessment, encryptedId).finally(() => asking.delete(assessment)));
  return asking.get(assessment);
}

function ask(assessment, encryptedId) {
  const dialog = document.getElementById("passphrase");
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
        if (!(await passphraseOpens(passphrase, encryptedId))) {
          error.hidden = false;
          form.elements.passphrase.select();
          return;
        }
        remember(assessment, passphrase, form.elements.remember.checked);
        document.dispatchEvent(new Event("gradeflow:passphrase-entered"));
        dialog.close("entered");
      },
      { capture: true, signal: asked.signal },
    );
    dialog.addEventListener(
      "close",
      () => {
        asked.abort();
        resolve(dialog.returnValue === "entered" ? form.elements.passphrase.value : null);
      },
      { signal: asked.signal },
    );
    dialog.returnValue = "";
    dialog.showModal();
    form.elements.passphrase.focus();
  });
}
