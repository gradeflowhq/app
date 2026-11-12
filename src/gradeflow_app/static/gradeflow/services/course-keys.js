// Course keys (courses/keys.py). Assessment pages carry the member's encrypted course key, or why they have none.

import { decryptWithKeyPair, encryptForPublicKey } from "../lib/account-crypto.js";
import { assessmentKeys, importCourseKey } from "../lib/assessment-crypto.js";
import { loadKeyPair, signedInAccount } from "./account.js";
import { send } from "./requests.js";

const courseKeyContext = (courseId, userId) => `gradeflow:course-key:${courseId}:${userId}`;

const decryptCourseKey = (keyPair, courseId, encryptedCourseKey) =>
  decryptWithKeyPair(keyPair, encryptedCourseKey, courseKeyContext(courseId, signedInAccount().userId));

/**
 * The assessment's keys, or null without a key pair or course key. With `create`, creates the course key when no member
 * holds it.
 */
export async function assessmentKeysFor(assessmentElement, { create = false } = {}) {
  const { course, assessment, encryptedCourseKey } = assessmentElement.dataset;
  const keyPair = await loadKeyPair();
  const canCreate = create && keyPair && !encryptedCourseKey;
  const courseKey = encryptedCourseKey || (canCreate ? await createCourseKey(assessmentElement) : "");
  if (!keyPair || !courseKey) return null;
  return assessmentKeys(await importCourseKey(await decryptCourseKey(keyPair, course, courseKey)), assessment);
}

export const missingCourseKeyMessage = (assessmentElement) =>
  assessmentElement.dataset.courseKeyUnavailableReason || "Reload the page to see student IDs.";

/** The new course key, encrypted for this member; empty when another member holds one. */
async function createCourseKey(assessmentElement) {
  const courseId = assessmentElement.dataset.course;
  const { publicKey, userId } = signedInAccount();
  const courseKey = crypto.getRandomValues(new Uint8Array(32));
  const encryptedKey = await encryptForPublicKey(publicKey, courseKey, courseKeyContext(courseId, userId));
  const response = await send(`/courses/${courseId}/key/`, { values: { encrypted_key: encryptedKey }, allowed: [409] });
  if (!response.ok) return "";
  assessmentElement.dataset.encryptedCourseKey = encryptedKey;
  return encryptedKey;
}

/** Encrypts this member's course keys for the members in `shares` who don't have them. */
export async function shareCourseKeys(shares) {
  const keyPair = await loadKeyPair();
  if (!keyPair) return;
  for (const { courseId, userId, publicKey, encryptedCourseKey } of shares) {
    const courseKey = await decryptCourseKey(keyPair, courseId, encryptedCourseKey);
    const encryptedKey = await encryptForPublicKey(publicKey, courseKey, courseKeyContext(courseId, userId));
    // 404: the member left or reset their password since the page loaded.
    await send(`/courses/${courseId}/members/${userId}/key/`, {
      values: { public_key: publicKey, encrypted_key: encryptedKey },
      allowed: [404],
    });
  }
}
