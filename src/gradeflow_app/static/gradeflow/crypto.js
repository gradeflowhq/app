/**
 * Encrypted student IDs: `enc:v1:<iv_base64>:<ciphertext_base64>`, with AES-256-GCM.
 *
 * HKDF-SHA256 derives each assessment's keys from the course key, with the assessment ID in the info: an AES key for
 * IDs, an HMAC-SHA256 key for their synthetic IVs (iv = HMAC(id)[0..12]) and an AES key for files. Synthetic IVs make
 * an ID encrypt the same way every time within an assessment, so encrypted IDs can be compared and looked up.
 *
 * Original submissions files: "GFF" | version 1 | random iv (12) | AES-256-GCM ciphertext and tag, with the 16-byte
 * header as additional data.
 */

const BASE64 = '[A-Za-z0-9+/=]+';
const ENCRYPTED_ID = new RegExp(`^enc:v1:(${BASE64}):(${BASE64})$`);

/** Matches encrypted IDs embedded anywhere in text (CSV, JSON, YAML). */
export const ENCRYPTED_IDS = new RegExp(`enc:v1:${BASE64}:${BASE64}`, 'g');

const encoder = new TextEncoder();
const decoder = new TextDecoder();

/** Imports a raw 32-byte course key for HKDF. */
export const importCourseKey = (bytes) => crypto.subtle.importKey('raw', bytes, 'HKDF', false, ['deriveKey']);

export const assessmentKeys = async (courseKey, assessmentId) => {
  const hkdf = (purpose) => ({ name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(), info: encoder.encode(`gradeflow:${purpose}:${assessmentId}`) });
  const aes = (purpose) => crypto.subtle.deriveKey(hkdf(purpose), courseKey, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
  const [idKey, syntheticIvKey, fileKey] = await Promise.all([
    aes('ids'),
    crypto.subtle.deriveKey(hkdf('synthetic-ivs'), courseKey, { name: 'HMAC', hash: 'SHA-256', length: 256 }, false, ['sign']),
    aes('files'),
  ]);
  return { idKey, syntheticIvKey, fileKey };
};

/** @returns {Promise<string>} */
export const encryptId = async (id, { idKey, syntheticIvKey }) => {
  const plaintext = encoder.encode(id.trim());
  const iv = new Uint8Array(await crypto.subtle.sign('HMAC', syntheticIvKey, plaintext), 0, 12);
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, idKey, plaintext));
  return `enc:v1:${iv.toBase64()}:${ciphertext.toBase64()}`;
};

/**
 * Rejects on a malformed encrypted ID and on keys it wasn't encrypted with (GCM tag mismatch).
 * @returns {Promise<string>}
 */
export const decryptId = async (encryptedId, { idKey }) => {
  const match = ENCRYPTED_ID.exec(encryptedId);
  if (!match) throw new Error('Invalid encrypted ID');
  const [, ivBase64, ciphertextBase64] = match;
  return decoder.decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: Uint8Array.fromBase64(ivBase64) }, idKey, Uint8Array.fromBase64(ciphertextBase64)));
};

/** Replaces every encrypted ID in `text` with its plaintext, leaving those that fail to decrypt. */
export const decryptText = async (text, keys) => {
  const encryptedIds = [...new Set(text.match(ENCRYPTED_IDS))];
  const results = await Promise.allSettled(encryptedIds.map((encryptedId) => decryptId(encryptedId, keys)));
  const plaintexts = new Map(encryptedIds.flatMap((encryptedId, i) =>
    results[i].status === 'fulfilled' ? [[encryptedId, results[i].value]] : []));
  return text.replace(ENCRYPTED_IDS, (encryptedId) => plaintexts.get(encryptedId) ?? encryptedId);
};

const FILE_MAGIC = [0x47, 0x46, 0x46, 1]; // "GFF", version 1
const FILE_HEADER_LENGTH = 16;

/** @returns {Promise<Uint8Array>} */
export const encryptFile = async (bytes, { fileKey }) => {
  const header = new Uint8Array(FILE_HEADER_LENGTH);
  header.set(FILE_MAGIC);
  header.set(crypto.getRandomValues(new Uint8Array(12)), 4);
  const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv: header.subarray(4), additionalData: header }, fileKey, bytes);
  const encryptedFile = new Uint8Array(FILE_HEADER_LENGTH + ciphertext.byteLength);
  encryptedFile.set(header);
  encryptedFile.set(new Uint8Array(ciphertext), FILE_HEADER_LENGTH);
  return encryptedFile;
};

/**
 * Rejects on another format and on other keys or tampering (GCM tag mismatch).
 * @returns {Promise<Uint8Array>}
 */
export const decryptFile = async (bytes, { fileKey }) => {
  if (bytes.length < FILE_HEADER_LENGTH + 16 || FILE_MAGIC.some((byte, i) => bytes[i] !== byte)) {
    throw new Error('Not an encrypted file');
  }
  const header = bytes.subarray(0, FILE_HEADER_LENGTH);
  return new Uint8Array(await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: header.subarray(4), additionalData: header }, fileKey, bytes.subarray(FILE_HEADER_LENGTH)));
};
