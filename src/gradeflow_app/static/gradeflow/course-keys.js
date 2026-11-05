// Course keys (courses/keys.py). Assessment pages carry the member's encrypted course key, or why they have none.

import { loadKeyPair, signedInAccount } from "./account.js";
import { decryptWithKeyPair, encryptForPublicKey } from "./account-crypto.js";
import { assessmentKeys, importCourseKey } from "./crypto.js";
import { send } from "./requests.js";

const courseKeyContext = (courseId, userId) => `gradeflow:course-key:${courseId}:${userId}`;

const decryptCourseKey = (keyPair, courseId, encryptedCourseKey) =>
  decryptWithKeyPair(keyPair, encryptedCourseKey, courseKeyContext(courseId, signedInAccount().userId));

/** The assessment's keys, or null without a key pair or course key. With `create`, creates the course key if none. */
export async function assessmentKeysFor(assessmentElement, { create = false } = {}) {
  const { course, assessment, encryptedCourseKey, courseKeyUnavailableReason } = assessmentElement.dataset;
  const keyPair = await loadKeyPair();
  const canCreate = create && keyPair && !encryptedCourseKey && !courseKeyUnavailableReason;
  const courseKey = encryptedCourseKey || (canCreate ? await createCourseKey(assessmentElement) : "");
  if (!keyPair || !courseKey) return null;
  return assessmentKeys(await importCourseKey(await decryptCourseKey(keyPair, course, courseKey)), assessment);
}

export const missingCourseKeyMessage = (assessmentElement) =>
  assessmentElement.dataset.courseKeyUnavailableReason || "Reload the page to see student IDs.";

/** The new course key, encrypted for this member; empty when another member created one first. */
async function createCourseKey(assessmentElement) {
  const courseId = assessmentElement.dataset.course;
  const { publicKey, userId } = signedInAccount();
  const courseKey = crypto.getRandomValues(new Uint8Array(32));
  const encryptedKey = await encryptForPublicKey(publicKey, courseKey, courseKeyContext(courseId, userId));
  const response = await send(`/courses/${courseId}/key/`, { values: { encrypted_key: encryptedKey }, allowed: [409] });
  return response.ok ? (assessmentElement.dataset.encryptedCourseKey = encryptedKey) : "";
}

/** Shares this member's course keys with members who don't have them (#pending-course-key-shares). */
async function shareCourseKeys() {
  const element = document.getElementById("pending-course-key-shares");
  const pendingShares = JSON.parse(element?.textContent || "[]");
  if (!pendingShares.length) return;
  element.textContent = "[]";
  const keyPair = await loadKeyPair();
  if (!keyPair) return;
  for (const { courseId, userId, publicKey, encryptedCourseKey } of pendingShares) {
    const courseKey = await decryptCourseKey(keyPair, courseId, encryptedCourseKey);
    const encryptedKey = await encryptForPublicKey(publicKey, courseKey, courseKeyContext(courseId, userId));
    // 404: the member left or reset their password since the page loaded.
    await send(`/courses/${courseId}/members/${userId}/key/`, { values: { public_key: publicKey, encrypted_key: encryptedKey }, allowed: [404] });
  }
}

const shareQuietly = () => shareCourseKeys().catch(() => {}); // the next page tries again
shareQuietly();
document.addEventListener("htmx:after:settle", shareQuietly);
