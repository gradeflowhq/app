// Shows the plaintext of encrypted student IDs (c-student-id) as they come into view, with the passphrase this browser keeps.
// Without one they show as encrypted, with a page's "Show student IDs" button (#show-student-ids) offering to show them; only
// that, or clicking an encrypted ID, asks for the passphrase.

import { decryptId } from "./crypto.js";
import { knownPassphrase, passphraseFor } from "./passphrase.js";

const showing = new WeakMap(); // each ID to its promise of being shown or left encrypted

function showDecryptedIds(elements) {
  const byAssessment = Map.groupBy(elements, (element) => element.closest("[data-assessment]")?.dataset.assessment);
  const shownPromises = [...byAssessment].map(([assessment, group]) => {
    const shownPromise = showAll(assessment, group);
    for (const element of group) {
      element.dataset.studentIdState = "decrypting";
      showing.set(element, shownPromise);
    }
    return shownPromise;
  });
  Promise.all(shownPromises).then(() => {
    const notice = document.getElementById("show-student-ids");
    if (notice) notice.hidden = !document.querySelector('[data-student-id-state="encrypted"]');
  });
}

async function showAll(assessment, group) {
  const passphrase = assessment && (await knownPassphrase(assessment, group[0].dataset.studentId));
  await Promise.all(group.map((element) => show(element, passphrase)));
}

async function show(element, passphrase) {
  const id = passphrase && (await decryptId(element.dataset.studentId, passphrase).catch(() => null));
  if (id) {
    element.textContent = id;
    element.dataset.studentIdState = "shown";
  } else {
    element.replaceChildren(Object.assign(document.createElement("span"), { className: "sr-only", textContent: "Encrypted" }));
    element.title = "Show student IDs";
    element.dataset.studentIdState = "encrypted";
  }
}

const visibilityObserver = new IntersectionObserver((entries) => {
  const visible = entries.filter((entry) => entry.isIntersecting).map((entry) => entry.target);
  for (const element of visible) visibilityObserver.unobserve(element);
  if (visible.length) showDecryptedIds(visible);
});

function watchNewIds() {
  for (const element of document.querySelectorAll("[data-student-id]:not([data-student-id-state])")) {
    element.dataset.studentIdState = "waiting";
    visibilityObserver.observe(element);
  }
}

/** Shows every ID inside `root` now, visible or not, and resolves once each is shown or left encrypted. */
export async function revealIn(root) {
  const elements = [...root.querySelectorAll("[data-student-id]")];
  const pending = elements.filter((element) => !["decrypting", "shown"].includes(element.dataset.studentIdState));
  for (const element of pending) visibilityObserver.unobserve(element);
  if (pending.length) showDecryptedIds(pending);
  await Promise.all(elements.map((element) => showing.get(element)));
}

/** Shows every ID again from scratch: after the passphrase is forgotten, or entered. */
function resetStudentIds() {
  for (const element of document.querySelectorAll("[data-student-id]")) {
    delete element.dataset.studentIdState;
    element.textContent = "…";
  }
  watchNewIds();
}

/** Asks for the passphrase, which shows every ID it opens. */
function askForPassphrase(assessment) {
  const encryptedId = document.querySelector(`[data-assessment="${assessment}"] [data-student-id-state="encrypted"]`)?.dataset.studentId;
  if (encryptedId) passphraseFor(assessment, encryptedId);
}

// Captured, so clicking an encrypted ID in a clickable row asks for the passphrase instead of opening the row.
document.addEventListener(
  "click",
  (event) => {
    const target = event.target.closest('[data-ask-passphrase], [data-student-id-state="encrypted"]');
    const assessment = target?.closest("[data-assessment]")?.dataset.assessment;
    if (!assessment) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    askForPassphrase(assessment);
  },
  true,
);

// Before any other listener, so what they reveal starts from scratch.
for (const name of ["gradeflow:passphrase-forgotten", "gradeflow:passphrase-entered"]) document.addEventListener(name, resetStudentIds);

new MutationObserver(watchNewIds).observe(document.documentElement, { childList: true, subtree: true });
watchNewIds();
