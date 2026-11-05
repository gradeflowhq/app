// Course keys (courses/keys.py): each course has a random key that encrypts its student IDs, and each member holds it
// encrypted for their public key. Assessment pages carry the member's encrypted course key
// (`data-encrypted-course-key`), or why they have none (`data-course-key-unavailable-reason`).

import { loadKeyPair, signedInAccount } from "./account.js";
import { decryptWithKeyPair, encryptForPublicKey } from "./account-crypto.js";
import { assessmentKeys, importCourseKey } from "./crypto.js";
import { send } from "./requests.js";

const courseKeyContext = (courseId, userId) => `gradeflow:course-key:${courseId}:${userId}`;

const decryptCourseKey = (keyPair, courseId, encryptedCourseKey) =>
  decryptWithKeyPair(keyPair, encryptedCourseKey, courseKeyContext(courseId, signedInAccount().userId));

/**
 * The keys of the assessment that `assessmentElement` (its `[data-assessment]` element) shows; null when this browser
 * can't load the key pair or the member has no course key. With `create`, a course without a key gets one.
 */
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

/** Creates the course's key, encrypted for this member; empty when another member created one first. */
async function createCourseKey(assessmentElement) {
  const courseId = assessmentElement.dataset.course;
  const { publicKey, userId } = signedInAccount();
  const courseKey = crypto.getRandomValues(new Uint8Array(32));
  const encryptedKey = await encryptForPublicKey(publicKey, courseKey, courseKeyContext(courseId, userId));
  const response = await send(`/courses/${courseId}/key/`, { values: { encrypted_key: encryptedKey }, allowed: [409] });
  return response.ok ? (assessmentElement.dataset.encryptedCourseKey = encryptedKey) : "";
}

/**
 * Encrypts the course keys this member has for the members who don't (#pending-course-key-shares), which the server
 * renders on each page and sends again after an invite. Emptied once read, so each list is shared once.
 */
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
    // 404: the member left, or reset their password, after this page was rendered.
    await send(`/courses/${courseId}/members/${userId}/key/`, { values: { public_key: publicKey, encrypted_key: encryptedKey }, allowed: [404] });
  }
}

const shareQuietly = () => shareCourseKeys().catch(() => {}); // the next page tries again
shareQuietly();
document.addEventListener("htmx:after:settle", shareQuietly);
