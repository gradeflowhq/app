/**
 * Encrypted student IDs: `enc:v2:<salt_base64>:<iv_base64>:<ciphertext_base64>`.
 *
 * salt = SHA-256("gradeflow:" + assessmentId)[0..16]; PBKDF2-HMAC-SHA256 (600k)
 * gives 512 bits = AES-256-GCM key || HMAC-SHA256 key; iv = HMAC(macKey, id)[0..12].
 * The synthetic IV makes encrypted IDs deterministic per assessment, so they work as
 * keys, while equal IVs only ever occur for equal plaintexts.
 *
 * Original submissions files: "GFF" | version 1 | salt (16) | random iv (12) | AES-256-GCM ciphertext and tag, with
 * the 32-byte header as additional data. The key is HKDF-SHA256 of the same 512 bits with info "gradeflow:file:v1",
 * so files never share a key with IDs.
 */

const ITERATIONS = 600_000;
const BASE64 = '[A-Za-z0-9+/=]+';
const ENCRYPTED_ID = new RegExp(`^enc:v2:(${BASE64}):(${BASE64}):(${BASE64})$`);

/** Matches encrypted IDs embedded anywhere in text (CSV, JSON, YAML). */
export const ENCRYPTED_IDS = new RegExp(`enc:v2:${BASE64}:${BASE64}:${BASE64}`, 'g');

const encoder = new TextEncoder();
const decoder = new TextDecoder();

/** Caches a promise per key, forgetting it if it rejects so a retry can succeed. */
const memoize = (fn) => {
  const cache = new Map();
  return (key, ...args) => {
    let value = cache.get(key);
    if (!value) {
      value = fn(...args);
      value.catch(() => cache.delete(key));
      cache.set(key, value);
    }
    return value;
  };
};

const deriveKeys = async (saltBase64, passphrase) => {
  const passphraseKey = await crypto.subtle.importKey('raw', encoder.encode(passphrase), 'PBKDF2', false, ['deriveBits']);
  const derivedBits = new Uint8Array(await crypto.subtle.deriveBits(
    { name: 'PBKDF2', hash: 'SHA-256', salt: Uint8Array.fromBase64(saltBase64), iterations: ITERATIONS },
    passphraseKey,
    512,
  ));
  const [idKey, macKey, fileKey] = await Promise.all([
    crypto.subtle.importKey('raw', derivedBits.slice(0, 32), 'AES-GCM', false, ['encrypt', 'decrypt']),
    crypto.subtle.importKey('raw', derivedBits.slice(32), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']),
    crypto.subtle.importKey('raw', derivedBits, 'HKDF', false, ['deriveKey']).then((master) => crypto.subtle.deriveKey(
      { name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(), info: encoder.encode('gradeflow:file:v1') },
      master,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    )),
  ]);
  return { idKey, macKey, fileKey };
};

const cachedKeys = memoize(deriveKeys);
const keys = (saltBase64, passphrase) => cachedKeys(`${saltBase64}:${passphrase}`, saltBase64, passphrase);

const assessmentSalt = async (assessmentId) => {
  const digest = await crypto.subtle.digest('SHA-256', encoder.encode(`gradeflow:${assessmentId}`));
  return new Uint8Array(digest, 0, 16).toBase64();
};

/** @returns {Promise<string>} */
export const encryptId = async (id, passphrase, assessmentId) => {
  const plaintext = encoder.encode(id.trim());
  const saltBase64 = await assessmentSalt(assessmentId);
  const { idKey, macKey } = await keys(saltBase64, passphrase);
  const iv = new Uint8Array(await crypto.subtle.sign('HMAC', macKey, plaintext), 0, 12);
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, idKey, plaintext));
  return `enc:v2:${saltBase64}:${iv.toBase64()}:${ciphertext.toBase64()}`;
};

/**
 * Rejects on a malformed encrypted ID and on a wrong passphrase (GCM tag mismatch).
 * @returns {Promise<string>}
 */
export const decryptId = async (encryptedId, passphrase) => {
  const match = ENCRYPTED_ID.exec(encryptedId);
  if (!match) throw new Error('Invalid encrypted ID');
  const [, saltBase64, ivBase64, ciphertextBase64] = match;
  const { idKey } = await keys(saltBase64, passphrase);
  return decoder.decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: Uint8Array.fromBase64(ivBase64) }, idKey, Uint8Array.fromBase64(ciphertextBase64)));
};

/** Replaces every encrypted ID in `text` with its plaintext, leaving those that fail to decrypt. */
export const decryptText = async (text, passphrase) => {
  const encryptedIds = [...new Set(text.match(ENCRYPTED_IDS))];
  const results = await Promise.allSettled(encryptedIds.map((encryptedId) => decryptId(encryptedId, passphrase)));
  const plaintexts = new Map(encryptedIds.flatMap((encryptedId, i) =>
    results[i].status === 'fulfilled' ? [[encryptedId, results[i].value]] : []));
  return text.replace(ENCRYPTED_IDS, (encryptedId) => plaintexts.get(encryptedId) ?? encryptedId);
};

const FILE_MAGIC = [0x47, 0x46, 0x46, 1]; // "GFF", version 1
const FILE_HEADER_LENGTH = 32;

/** @returns {Promise<Uint8Array>} `bytes` encrypted for the assessment's members */
export const encryptFile = async (bytes, passphrase, assessmentId) => {
  const saltBase64 = await assessmentSalt(assessmentId);
  const header = new Uint8Array(FILE_HEADER_LENGTH);
  header.set(FILE_MAGIC);
  header.set(Uint8Array.fromBase64(saltBase64), 4);
  header.set(crypto.getRandomValues(new Uint8Array(12)), 20);
  const { fileKey } = await keys(saltBase64, passphrase);
  const ciphertext = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: header.subarray(20), additionalData: header }, fileKey, bytes);
  const encryptedFile = new Uint8Array(FILE_HEADER_LENGTH + ciphertext.byteLength);
  encryptedFile.set(header);
  encryptedFile.set(new Uint8Array(ciphertext), FILE_HEADER_LENGTH);
  return encryptedFile;
};

/**
 * Rejects on another format and on a wrong passphrase or tampering (GCM tag mismatch).
 * @returns {Promise<Uint8Array>}
 */
export const decryptFile = async (bytes, passphrase) => {
  if (bytes.length < FILE_HEADER_LENGTH + 16 || FILE_MAGIC.some((byte, i) => bytes[i] !== byte)) {
    throw new Error('Not an encrypted file');
  }
  const header = bytes.subarray(0, FILE_HEADER_LENGTH);
  const { fileKey } = await keys(header.subarray(4, 20).toBase64(), passphrase);
  return new Uint8Array(await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: header.subarray(20), additionalData: header }, fileKey, bytes.subarray(FILE_HEADER_LENGTH)));
};
