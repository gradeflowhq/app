import assert from 'node:assert/strict';
import { createCipheriv, createDecipheriv, createHash, createHmac, hkdfSync, pbkdf2Sync } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { decryptFile, decryptId, decryptText, encryptFile, encryptId, ENCRYPTED_IDS } from '../../src/gradeflow_app/static/gradeflow/crypto.js';

const PASSPHRASE = 'correct horse battery';

/** The v2 scheme written independently with node:crypto. */
const referenceEncryptId = (id, passphrase, assessmentId) => {
  const salt = createHash('sha256').update(`gradeflow:${assessmentId}`).digest().subarray(0, 16);
  const derivedKey = pbkdf2Sync(passphrase, salt, 600_000, 64, 'sha256');
  const plaintext = Buffer.from(id.trim());
  const iv = createHmac('sha256', derivedKey.subarray(32)).update(plaintext).digest().subarray(0, 12);
  const cipher = createCipheriv('aes-256-gcm', derivedKey.subarray(0, 32), iv);
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final(), cipher.getAuthTag()]);
  return ['enc', 'v2', salt, iv, ciphertext].map((part) => (Buffer.isBuffer(part) ? part.toString('base64') : part)).join(':');
};

test('matches an independent implementation of the scheme in both directions', async () => {
  const expected = referenceEncryptId(' S1234567 ', PASSPHRASE, 'assessment-1');
  assert.match(expected, /^enc:v2:[A-Za-z0-9+/=]{24}:[A-Za-z0-9+/=]{16}:[A-Za-z0-9+/=]+$/);
  assert.equal(await encryptId('S1234567', PASSPHRASE, 'assessment-1'), expected);
  assert.equal(await decryptId(expected, PASSPHRASE), 'S1234567');
});

test('round trips and trims the id', async () => {
  const encryptedId = await encryptId('  ann@example.edu\n', PASSPHRASE, 'assessment-1');
  assert.match(encryptedId, /^enc:v2:/);
  assert.equal(await decryptId(encryptedId, PASSPHRASE), 'ann@example.edu');
});

test('is deterministic within an assessment and differs across assessments', async () => {
  const firstEncryptedId = await encryptId('S1000001', PASSPHRASE, 'assessment-1');
  assert.equal(await encryptId('S1000001', PASSPHRASE, 'assessment-1'), firstEncryptedId);
  assert.notEqual(await encryptId('S1000002', PASSPHRASE, 'assessment-1'), firstEncryptedId);
  assert.notEqual(await encryptId('S1000001', PASSPHRASE, 'assessment-2'), firstEncryptedId);
});

test('rejects a wrong passphrase and malformed encrypted IDs', async () => {
  const encryptedId = await encryptId('S1000001', PASSPHRASE, 'assessment-1');
  await assert.rejects(decryptId(encryptedId, 'wrong passphrase'));
  await assert.rejects(decryptId('enc:v2:abc', PASSPHRASE), /Invalid encrypted ID/);
  await assert.rejects(decryptId(encryptedId.replace('v2', 'v1'), PASSPHRASE), /Invalid encrypted ID/);
  await assert.rejects(decryptId(`${encryptedId}x`, PASSPHRASE));
});

test('decryptText replaces every encryptedId it can open', async () => {
  const one = await encryptId('S1000001', PASSPHRASE, 'assessment-1');
  const two = await encryptId('S1000002', PASSPHRASE, 'assessment-1');
  const foreign = await encryptId('S1000003', 'another passphrase', 'assessment-1');
  const csv = `student_id,total\n${one},3\n${two},4\n${one},5\n${foreign},6\n`;

  assert.equal(
    await decryptText(csv, PASSPHRASE),
    `student_id,total\nS1000001,3\nS1000002,4\nS1000001,5\n${foreign},6\n`,
  );
  assert.equal(await decryptText(JSON.stringify({ id: one }), PASSPHRASE), '{"id":"S1000001"}');
  assert.equal(await decryptText('no encrypted IDs here', PASSPHRASE), 'no encrypted IDs here');
  assert.equal(ENCRYPTED_IDS.lastIndex, 0);
});

/** The file format read independently with node:crypto. */
const referenceDecryptFile = (bytes, passphrase) => {
  const data = Buffer.from(bytes);
  const header = data.subarray(0, 32);
  const derivedKey = pbkdf2Sync(passphrase, header.subarray(4, 20), 600_000, 64, 'sha256');
  const key = Buffer.from(hkdfSync('sha256', derivedKey, Buffer.alloc(0), 'gradeflow:file:v1', 32));
  const decipher = createDecipheriv('aes-256-gcm', key, header.subarray(20, 32));
  decipher.setAAD(header);
  decipher.setAuthTag(data.subarray(-16));
  return Buffer.concat([decipher.update(data.subarray(32, -16)), decipher.final()]);
};

const FILE = new TextEncoder().encode('StudentID,Q1\nS1000001,yes\n');

test('encryptFile writes GFF, version 1, the assessment salt, a random IV and an authenticated ciphertext', async () => {
  const encrypted = await encryptFile(FILE, PASSPHRASE, 'assessment-1');
  const salt = createHash('sha256').update('gradeflow:assessment-1').digest().subarray(0, 16);
  assert.deepEqual([...encrypted.subarray(0, 4)], [0x47, 0x46, 0x46, 1]);
  assert.deepEqual(Buffer.from(encrypted.subarray(4, 20)), salt);
  assert.equal(encrypted.length, 32 + FILE.length + 16);
  assert.deepEqual(referenceDecryptFile(encrypted, PASSPHRASE), Buffer.from(FILE));

  const again = await encryptFile(FILE, PASSPHRASE, 'assessment-1');
  assert.notDeepEqual(again.subarray(20, 32), encrypted.subarray(20, 32));
});

test('decryptFile round trips and rejects a wrong passphrase, tampering and other formats', async () => {
  const encrypted = await encryptFile(FILE, PASSPHRASE, 'assessment-1');
  assert.deepEqual(await decryptFile(encrypted, PASSPHRASE), FILE);
  await assert.rejects(decryptFile(encrypted, 'wrong passphrase'));

  const tampered = encrypted.slice();
  tampered[40] ^= 1;
  await assert.rejects(decryptFile(tampered, PASSPHRASE));
  const otherVersion = encrypted.slice();
  otherVersion[3] = 2;
  await assert.rejects(decryptFile(otherVersion, PASSPHRASE), /Not an encrypted file/);
  await assert.rejects(decryptFile(FILE, PASSPHRASE), /Not an encrypted file/);
});

// The server checks the same fixture's ID and file (tests/test_imports.py).
const GOLDEN = JSON.parse(readFileSync(new URL('../fixtures/encrypted.json', import.meta.url)));

test('reproduces the golden encrypted ID and opens the golden file', async () => {
  assert.equal(await encryptId(GOLDEN.student_id, GOLDEN.passphrase, GOLDEN.assessment_id), GOLDEN.encrypted_id);
  const file = await decryptFile(Uint8Array.fromBase64(GOLDEN.encrypted_file), GOLDEN.passphrase);
  assert.equal(new TextDecoder().decode(file), GOLDEN.file);
});
