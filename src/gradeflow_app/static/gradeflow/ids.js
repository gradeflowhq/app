// Decrypts student IDs (c-student-id) as they appear. Without the course key they stay encrypted.

import { assessmentKeysFor } from "./course-keys.js";
import { decryptId } from "./crypto.js";

const showing = new WeakMap(); // each ID to its promise of being shown or left encrypted

function showDecryptedIds(elements) {
  for (const [assessmentElement, group] of Map.groupBy(elements, (element) => element.closest("[data-assessment]"))) {
    const shownPromise = showAll(assessmentElement, group);
    for (const element of group) {
      element.dataset.studentIdState = "decrypting";
      showing.set(element, shownPromise);
    }
  }
}

async function showAll(assessmentElement, group) {
  const keys = assessmentElement && (await assessmentKeysFor(assessmentElement).catch(() => null));
  await Promise.all(group.map((element) => show(element, keys)));
}

async function show(element, keys) {
  const id = keys && (await decryptId(element.dataset.studentId, keys).catch(() => null));
  if (id) {
    element.textContent = id;
    element.dataset.studentIdState = "shown";
  } else {
    element.replaceChildren(Object.assign(document.createElement("span"), { className: "sr-only", textContent: "Encrypted" }));
    element.dataset.studentIdState = "encrypted";
  }
}

const NEW_IDS = "[data-student-id]:not([data-student-id-state])";

function showNewIds() {
  const elements = [...document.querySelectorAll(NEW_IDS)];
  if (elements.length) showDecryptedIds(elements);
}

/** Resolves once every ID inside `root` is shown or left encrypted. */
export async function revealIn(root) {
  showNewIds();
  await Promise.all([...root.querySelectorAll("[data-student-id]")].map((element) => showing.get(element)));
}

new MutationObserver(showNewIds).observe(document.documentElement, { childList: true, subtree: true });
showNewIds();
