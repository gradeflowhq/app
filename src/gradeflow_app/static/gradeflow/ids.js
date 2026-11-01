// Shows the plaintext of student ID envelopes (c-sid) as they come into view, with the passphrase this browser keeps.
// Without one they show as encrypted, with a page's "Show student IDs" button (#locked-ids) offering to show them; only
// that, or clicking an encrypted ID, asks for the passphrase.

import { decryptId } from "./crypto.js";
import { known, passphraseFor } from "./passphrase.js";

const revealing = new WeakMap(); // each ID to its promise of being shown or locked

function reveal(elements) {
  const byAssessment = Map.groupBy(elements, (element) => element.closest("[data-assessment]")?.dataset.assessment);
  const revealed = [...byAssessment].map(([assessment, group]) => {
    const done = showAll(assessment, group);
    for (const element of group) {
      element.dataset.sidState = "opening";
      revealing.set(element, done);
    }
    return done;
  });
  Promise.all(revealed).then(() => {
    const notice = document.getElementById("locked-ids");
    if (notice) notice.hidden = !document.querySelector('[data-sid-state="locked"]');
  });
}

async function showAll(assessment, group) {
  const passphrase = assessment && (await known(assessment, group[0].dataset.sid));
  await Promise.all(group.map((element) => show(element, passphrase)));
}

async function show(element, passphrase) {
  const id = passphrase && (await decryptId(element.dataset.sid, passphrase).catch(() => null));
  if (id) {
    element.textContent = id;
    element.dataset.sidState = "shown";
  } else {
    element.replaceChildren(Object.assign(document.createElement("span"), { className: "sr-only", textContent: "Encrypted" }));
    element.title = "Show student IDs";
    element.dataset.sidState = "locked";
  }
}

const visible = new IntersectionObserver((entries) => {
  const found = entries.filter((entry) => entry.isIntersecting).map((entry) => entry.target);
  for (const element of found) visible.unobserve(element);
  if (found.length) reveal(found);
});

function watch() {
  for (const element of document.querySelectorAll("[data-sid]:not([data-sid-state])")) {
    element.dataset.sidState = "waiting";
    visible.observe(element);
  }
}

/** Shows every ID inside `root` now, visible or not, and resolves once each is shown or locked. */
export async function revealIn(root) {
  const elements = [...root.querySelectorAll("[data-sid]")];
  const pending = elements.filter((element) => !["opening", "shown"].includes(element.dataset.sidState));
  for (const element of pending) visible.unobserve(element);
  if (pending.length) reveal(pending);
  await Promise.all(elements.map((element) => revealing.get(element)));
}

/** Shows every ID again from scratch: after the passphrase is forgotten, or entered. */
function relock() {
  for (const element of document.querySelectorAll("[data-sid]")) {
    delete element.dataset.sidState;
    element.textContent = "…";
  }
  watch();
}

/** Asks for the passphrase, which shows every ID it opens. */
function unlock(assessment) {
  const envelope = document.querySelector(`[data-assessment="${assessment}"] [data-sid-state="locked"]`)?.dataset.sid;
  if (envelope) passphraseFor(assessment, envelope);
}

// Captured, so clicking an encrypted ID in a clickable row asks for the passphrase instead of opening the row.
document.addEventListener(
  "click",
  (event) => {
    const target = event.target.closest('[data-unlock], [data-sid-state="locked"]');
    const assessment = target?.closest("[data-assessment]")?.dataset.assessment;
    if (!assessment) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    unlock(assessment);
  },
  true,
);

// Before any other listener, so what they reveal starts from scratch.
for (const name of ["gradeflow:locked", "gradeflow:unlocked"]) document.addEventListener(name, relock);

new MutationObserver(watch).observe(document.documentElement, { childList: true, subtree: true });
watch();
