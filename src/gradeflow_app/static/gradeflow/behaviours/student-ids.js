// Student IDs (c-student-id) decrypt as they appear. Without the course key they stay encrypted.

import { decryptId } from "../lib/assessment-crypto.js";
import { assessmentKeysFor } from "../services/course-keys.js";

const NEW_IDS = "[data-student-id]:not([data-student-id-state])";
const showing = new WeakMap(); // an ID's element → the promise of its group being shown or left encrypted

async function show(element, keys) {
  const id = keys && (await decryptId(element.dataset.studentId, keys).catch(() => null));
  if (id) {
    element.textContent = id;
    element.dataset.studentIdState = "shown";
  } else {
    element.replaceChildren(
      Object.assign(document.createElement("span"), { className: "sr-only", textContent: "Encrypted" }),
    );
    element.dataset.studentIdState = "encrypted";
  }
}

async function showGroup(assessmentElement, elements) {
  const keys = assessmentElement && (await assessmentKeysFor(assessmentElement).catch(() => null));
  await Promise.all(elements.map((element) => show(element, keys)));
}

function showNewIds() {
  const elements = [...document.querySelectorAll(NEW_IDS)];
  for (const [assessmentElement, group] of Map.groupBy(elements, (element) => element.closest("[data-assessment]"))) {
    const shown = showGroup(assessmentElement, group);
    for (const element of group) {
      element.dataset.studentIdState = "decrypting";
      showing.set(element, shown);
    }
  }
}

/** Resolves once every ID inside `root` is shown or left encrypted. */
export async function revealIn(root) {
  showNewIds();
  await Promise.all([...root.querySelectorAll("[data-student-id]")].map((element) => showing.get(element)));
}

export function start() {
  new MutationObserver(showNewIds).observe(document.documentElement, { childList: true, subtree: true });
  showNewIds();
}
