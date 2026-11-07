import assert from "node:assert/strict";
import { createCipheriv, createDecipheriv, createHmac, hkdfSync } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  assessmentKeys,
  decryptFile,
  decryptId,
  decryptText,
  ENCRYPTED_IDS,
  encryptFile,
  encryptId,
  importCourseKey,
} from "../../src/gradeflow_app/static/gradeflow/lib/assessment-crypto.js";

const COURSE_KEY = Buffer.alloc(32, 7);
const OTHER_COURSE_KEY = Buffer.alloc(32, 8);
const keysOf = async (courseKey, assessmentId) => assessmentKeys(await importCourseKey(courseKey), assessmentId);
const KEYS = await keysOf(COURSE_KEY, "assessment-1");

/** The scheme written independently with node:crypto. */
const reference = (courseKey, assessmentId) => {
  const key = (purpose) =>
    Buffer.from(hkdfSync("sha256", courseKey, Buffer.alloc(0), `gradeflow:${purpose}:${assessmentId}`, 32));
  return { idKey: key("ids"), syntheticIvKey: key("synthetic-ivs"), fileKey: key("files") };
};

const referenceEncryptId = (id, courseKey, assessmentId) => {
  const { idKey, syntheticIvKey } = reference(courseKey, assessmentId);
  const plaintext = Buffer.from(id.trim());
  const iv = createHmac("sha256", syntheticIvKey).update(plaintext).digest().subarray(0, 12);
  const cipher = createCipheriv("aes-256-gcm", idKey, iv);
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final(), cipher.getAuthTag()]);
  return `enc:v1:${iv.toString("base64")}:${ciphertext.toString("base64")}`;
};

const referenceDecryptFile = (bytes, courseKey, assessmentId) => {
  const data = Buffer.from(bytes);
  const header = data.subarray(0, 16);
  const decipher = createDecipheriv("aes-256-gcm", reference(courseKey, assessmentId).fileKey, header.subarray(4));
  decipher.setAAD(header);
  decipher.setAuthTag(data.subarray(-16));
  return Buffer.concat([decipher.update(data.subarray(16, -16)), decipher.final()]);
};

test("matches an independent implementation of the scheme in both directions", async () => {
  const expected = referenceEncryptId(" S1234567 ", COURSE_KEY, "assessment-1");
  assert.match(expected, /^enc:v1:[A-Za-z0-9+/=]{16}:[A-Za-z0-9+/=]+$/);
  assert.equal(await encryptId("S1234567", KEYS), expected);
  assert.equal(await decryptId(expected, KEYS), "S1234567");
});

test("round trips and trims the id", async () => {
  const encryptedId = await encryptId("  ann@example.edu\n", KEYS);
  assert.equal(await decryptId(encryptedId, KEYS), "ann@example.edu");
});

test("is deterministic within an assessment and differs across assessments and courses", async () => {
  const firstEncryptedId = await encryptId("S1000001", KEYS);
  assert.equal(await encryptId("S1000001", KEYS), firstEncryptedId);
  assert.notEqual(await encryptId("S1000002", KEYS), firstEncryptedId);
  assert.notEqual(await encryptId("S1000001", await keysOf(COURSE_KEY, "assessment-2")), firstEncryptedId);
  assert.notEqual(await encryptId("S1000001", await keysOf(OTHER_COURSE_KEY, "assessment-1")), firstEncryptedId);
});

test("rejects other keys and malformed encrypted IDs", async () => {
  const encryptedId = await encryptId("S1000001", KEYS);
  await assert.rejects(decryptId(encryptedId, await keysOf(COURSE_KEY, "assessment-2")));
  await assert.rejects(decryptId("enc:v1:abc", KEYS), /Invalid encrypted ID/);
  await assert.rejects(decryptId(encryptedId.replace("v1", "v2"), KEYS), /Invalid encrypted ID/);
  await assert.rejects(decryptId(`${encryptedId}x`, KEYS));
});

test("decryptText replaces every encrypted ID it can open", async () => {
  const one = await encryptId("S1000001", KEYS);
  const two = await encryptId("S1000002", KEYS);
  const foreign = await encryptId("S1000003", await keysOf(OTHER_COURSE_KEY, "assessment-1"));
  const csv = `student_id,total\n${one},3\n${two},4\n${one},5\n${foreign},6\n`;

  assert.equal(await decryptText(csv, KEYS), `student_id,total\nS1000001,3\nS1000002,4\nS1000001,5\n${foreign},6\n`);
  assert.equal(await decryptText(JSON.stringify({ id: one }), KEYS), '{"id":"S1000001"}');
  assert.equal(await decryptText("no encrypted IDs here", KEYS), "no encrypted IDs here");
  assert.equal(ENCRYPTED_IDS.lastIndex, 0);
});

const FILE = new TextEncoder().encode("StudentID,Q1\nS1000001,yes\n");

test("encryptFile writes GFF, version 1, a random IV and an authenticated ciphertext", async () => {
  const encrypted = await encryptFile(FILE, KEYS);
  assert.deepEqual([...encrypted.subarray(0, 4)], [0x47, 0x46, 0x46, 1]);
  assert.equal(encrypted.length, 16 + FILE.length + 16);
  assert.deepEqual(referenceDecryptFile(encrypted, COURSE_KEY, "assessment-1"), Buffer.from(FILE));
  assert.notDeepEqual((await encryptFile(FILE, KEYS)).subarray(4, 16), encrypted.subarray(4, 16));
});

test("decryptFile round trips and rejects other keys, tampering and other formats", async () => {
  const encrypted = await encryptFile(FILE, KEYS);
  assert.deepEqual(await decryptFile(encrypted, KEYS), FILE);
  await assert.rejects(decryptFile(encrypted, await keysOf(COURSE_KEY, "assessment-2")));

  const tampered = encrypted.slice();
  tampered[20] ^= 1;
  await assert.rejects(decryptFile(tampered, KEYS));
  const otherVersion = encrypted.slice();
  otherVersion[3] = 2;
  await assert.rejects(decryptFile(otherVersion, KEYS), /Not an encrypted file/);
  await assert.rejects(decryptFile(FILE, KEYS), /Not an encrypted file/);
});

// The server checks the same fixture's ID and file (tests/test_imports.py).
const GOLDEN = JSON.parse(readFileSync(new URL("../fixtures/encrypted.json", import.meta.url)));

test("reproduces the golden encrypted ID and opens the golden file", async () => {
  const keys = await keysOf(Uint8Array.fromHex(GOLDEN.course_key), GOLDEN.assessment_id);
  assert.equal(await encryptId(GOLDEN.student_id, keys), GOLDEN.encrypted_id);
  const file = await decryptFile(Uint8Array.fromBase64(GOLDEN.encrypted_file), keys);
  assert.equal(new TextDecoder().decode(file), GOLDEN.file);
});
