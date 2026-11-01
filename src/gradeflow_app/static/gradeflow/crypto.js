/**
 * Student ID envelopes: `enc:v2:<salt_b64>:<iv_b64>:<ciphertext_b64>`.
 *
 * salt = SHA-256("gradeflow:" + assessmentId)[0..16]; PBKDF2-HMAC-SHA256 (600k)
 * gives 512 bits = AES-256-GCM key || HMAC-SHA256 key; iv = HMAC(macKey, id)[0..12].
 * The synthetic IV makes envelopes deterministic per assessment, so they work as
 * keys, while equal IVs only ever occur for equal plaintexts.
 *
 * Original results files: "GFF" | version 1 | salt (16) | random iv (12) | AES-256-GCM ciphertext and tag, with
 * the 32-byte header as additional data. The key is HKDF-SHA256 of the same 512 bits with info "gradeflow:file:v1",
 * so files never share a key with IDs.
 */

const ITERATIONS = 600_000;
const B64 = '[A-Za-z0-9+/=]+';
const ENVELOPE = new RegExp(`^enc:v2:(${B64}):(${B64}):(${B64})$`);

/** Matches envelopes embedded anywhere in text (CSV, JSON, YAML). */
export const ENVELOPES = new RegExp(`enc:v2:${B64}:${B64}:${B64}`, 'g');

const encoder = new TextEncoder();
const decoder = new TextDecoder();

const toB64 = (bytes) => btoa(String.fromCharCode(...bytes));
const fromB64 = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

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

const deriveKeys = async (saltB64, passphrase) => {
  const material = await crypto.subtle.importKey('raw', encoder.encode(passphrase), 'PBKDF2', false, ['deriveBits']);
  const bits = new Uint8Array(await crypto.subtle.deriveBits(
    { name: 'PBKDF2', hash: 'SHA-256', salt: fromB64(saltB64), iterations: ITERATIONS },
    material,
    512,
  ));
  const [enc, mac, file] = await Promise.all([
    crypto.subtle.importKey('raw', bits.slice(0, 32), 'AES-GCM', false, ['encrypt', 'decrypt']),
    crypto.subtle.importKey('raw', bits.slice(32), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']),
    crypto.subtle.importKey('raw', bits, 'HKDF', false, ['deriveKey']).then((master) => crypto.subtle.deriveKey(
      { name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(), info: encoder.encode('gradeflow:file:v1') },
      master,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    )),
  ]);
  return { enc, mac, file };
};

const cachedKeys = memoize(deriveKeys);
const keys = (saltB64, passphrase) => cachedKeys(`${saltB64}:${passphrase}`, saltB64, passphrase);

const assessmentSalt = async (assessmentId) => {
  const digest = await crypto.subtle.digest('SHA-256', encoder.encode(`gradeflow:${assessmentId}`));
  return toB64(new Uint8Array(digest, 0, 16));
};

/** @returns {Promise<string>} */
export const encryptId = async (id, passphrase, assessmentId) => {
  const plaintext = encoder.encode(id.trim());
  const saltB64 = await assessmentSalt(assessmentId);
  const { enc, mac } = await keys(saltB64, passphrase);
  const iv = new Uint8Array(await crypto.subtle.sign('HMAC', mac, plaintext), 0, 12);
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, enc, plaintext));
  return `enc:v2:${saltB64}:${toB64(iv)}:${toB64(ciphertext)}`;
};

const decrypt = async (envelope, passphrase) => {
  const match = ENVELOPE.exec(envelope);
  if (!match) throw new Error('Invalid envelope');
  const [, saltB64, ivB64, ciphertextB64] = match;
  const { enc } = await keys(saltB64, passphrase);
  return decoder.decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: fromB64(ivB64) }, enc, fromB64(ciphertextB64)));
};

const cachedDecrypt = memoize(decrypt);

/**
 * Rejects on a malformed envelope and on a wrong passphrase (GCM tag mismatch).
 * @returns {Promise<string>}
 */
export const decryptId = (envelope, passphrase) =>
  cachedDecrypt(`${passphrase}\n${envelope}`, envelope, passphrase);

/** Replaces every envelope in `text` with its plaintext, leaving those that fail to decrypt. */
export const decryptText = async (text, passphrase) => {
  const envelopes = [...new Set(text.match(ENVELOPES))];
  const results = await Promise.allSettled(envelopes.map((envelope) => decryptId(envelope, passphrase)));
  const plaintexts = new Map(envelopes.flatMap((envelope, i) =>
    results[i].status === 'fulfilled' ? [[envelope, results[i].value]] : []));
  return text.replace(ENVELOPES, (envelope) => plaintexts.get(envelope) ?? envelope);
};

const FILE_MAGIC = [0x47, 0x46, 0x46, 1]; // "GFF", version 1
const FILE_HEADER = 32;

/** @returns {Promise<Uint8Array>} `bytes` encrypted for the assessment's members */
export const encryptFile = async (bytes, passphrase, assessmentId) => {
  const saltB64 = await assessmentSalt(assessmentId);
  const header = new Uint8Array(FILE_HEADER);
  header.set(FILE_MAGIC);
  header.set(fromB64(saltB64), 4);
  header.set(crypto.getRandomValues(new Uint8Array(12)), 20);
  const { file } = await keys(saltB64, passphrase);
  const ciphertext = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: header.subarray(20), additionalData: header }, file, bytes);
  const out = new Uint8Array(FILE_HEADER + ciphertext.byteLength);
  out.set(header);
  out.set(new Uint8Array(ciphertext), FILE_HEADER);
  return out;
};

/**
 * Rejects on another format and on a wrong passphrase or tampering (GCM tag mismatch).
 * @returns {Promise<Uint8Array>}
 */
export const decryptFile = async (bytes, passphrase) => {
  if (bytes.length < FILE_HEADER + 16 || FILE_MAGIC.some((byte, i) => bytes[i] !== byte)) {
    throw new Error('Not an encrypted file');
  }
  const header = bytes.subarray(0, FILE_HEADER);
  const { file } = await keys(toB64(header.subarray(4, 20)), passphrase);
  return new Uint8Array(await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: header.subarray(20), additionalData: header }, file, bytes.subarray(FILE_HEADER)));
};
