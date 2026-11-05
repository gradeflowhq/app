// Account keys. Argon2id and HKDF derive two keys from the password: an authentication key, sent in its place, and a
// wrapping key, which wraps the account's X25519 private key and never leaves the browser. HPKE (RFC 9180) encrypts
// course keys and secrets for a public key.

import { Aes128Gcm, CipherSuite, DhkemX25519HkdfSha256, HkdfSha256 } from "../vendor/hpke-core-1.9.0/hpke.min.js";
import { argon2idAsync } from "../vendor/noble-hashes-2.4.0/argon2.min.js";

const ARGON2_OPTIONS = { t: 3, m: 64 * 1024, p: 1, dkLen: 32, asyncTick: 10 };
const KDF_SALT_SIZE = 16;
const RECOVERY_KEY_SIZE = 16;

const encoder = new TextEncoder();
const hpke = new CipherSuite({ kem: new DhkemX25519HkdfSha256(), kdf: new HkdfSha256(), aead: new Aes128Gcm() });

export const generateKdfSalt = () => crypto.getRandomValues(new Uint8Array(KDF_SALT_SIZE)).toBase64();

/** The authentication key (hex) and the wrapping key (AES-GCM, not extractable) derived from `password`. */
export async function deriveKeysFromPassword(password, kdfSalt) {
  const secret = await argon2idAsync(password.normalize("NFC"), Uint8Array.fromBase64(kdfSalt), ARGON2_OPTIONS);
  const master = await crypto.subtle.importKey("raw", secret, "HKDF", false, ["deriveBits", "deriveKey"]);
  const hkdf = (info) => ({ name: "HKDF", hash: "SHA-256", salt: new Uint8Array(), info: encoder.encode(info) });
  const [authKey, wrappingKey] = await Promise.all([
    crypto.subtle.deriveBits(hkdf("gradeflow:auth"), master, 256),
    crypto.subtle.deriveKey(hkdf("gradeflow:wrapping"), master, { name: "AES-GCM", length: 256 }, false, ["wrapKey", "unwrapKey"]),
  ]);
  return { authKey: new Uint8Array(authKey).toHex(), wrappingKey };
}

/** 128 random bits as eight groups of four hex digits. */
export const generateRecoveryKey = () =>
  crypto.getRandomValues(new Uint8Array(RECOVERY_KEY_SIZE)).toHex().match(/.{4}/g).join("-");

/** The recovery key as an AES-GCM wrapping key. Rejects anything but 32 hex digits, ignoring spaces and dashes. */
export async function importRecoveryKey(recoveryKey) {
  const bytes = Uint8Array.fromHex(recoveryKey.replace(/[\s-]/g, "").toLowerCase());
  if (bytes.length !== RECOVERY_KEY_SIZE) throw new Error("Not a recovery key");
  return crypto.subtle.importKey("raw", bytes, "AES-GCM", false, ["wrapKey", "unwrapKey"]);
}

const importPublicKey = (publicKey) => crypto.subtle.importKey("raw", Uint8Array.fromBase64(publicKey), "X25519", true, []);

/** The IV, then the private key in PKCS #8 wrapped with AES-GCM, in base64. */
async function wrapPrivateKey(privateKey, wrappingKey) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const wrapped = await crypto.subtle.wrapKey("pkcs8", privateKey, wrappingKey, { name: "AES-GCM", iv });
  return new Uint8Array([...iv, ...new Uint8Array(wrapped)]).toBase64();
}

function unwrapPrivateKey(wrappedPrivateKey, wrappingKey, extractable) {
  const bytes = Uint8Array.fromBase64(wrappedPrivateKey);
  const algorithm = { name: "AES-GCM", iv: bytes.subarray(0, 12) };
  return crypto.subtle.unwrapKey("pkcs8", bytes.subarray(12), wrappingKey, algorithm, "X25519", extractable, ["deriveBits"]);
}

/** A new X25519 key pair: the public key, and the private key wrapped with `wrappingKey`. */
export async function generateKeyPair(wrappingKey) {
  const { privateKey, publicKey } = await crypto.subtle.generateKey("X25519", true, ["deriveBits"]);
  return {
    publicKey: new Uint8Array(await crypto.subtle.exportKey("raw", publicKey)).toBase64(),
    wrappedPrivateKey: await wrapPrivateKey(privateKey, wrappingKey),
  };
}

/** Rejects when `oldWrappingKey` doesn't unwrap the private key. */
export const rewrapPrivateKey = async (wrappedPrivateKey, oldWrappingKey, newWrappingKey) =>
  wrapPrivateKey(await unwrapPrivateKey(wrappedPrivateKey, oldWrappingKey, true), newWrappingKey);

/** Rejects when `wrappingKey` doesn't unwrap the private key. */
export async function unwrapKeyPair({ publicKey, wrappedPrivateKey }, wrappingKey) {
  return {
    privateKey: await unwrapPrivateKey(wrappedPrivateKey, wrappingKey, false),
    publicKey: await importPublicKey(publicKey),
  };
}

/** The encapsulated key, then the ciphertext, in base64. */
export async function encryptForPublicKey(publicKey, bytes, context) {
  const { enc, ct } = await hpke.seal({ recipientPublicKey: await importPublicKey(publicKey), info: encoder.encode(context) }, bytes);
  return new Uint8Array([...new Uint8Array(enc), ...new Uint8Array(ct)]).toBase64();
}

/** Rejects when `encrypted` wasn't encrypted for `keyPair` with the same `context`. */
export async function decryptWithKeyPair(keyPair, encrypted, context) {
  const bytes = Uint8Array.fromBase64(encrypted);
  const enc = bytes.subarray(0, hpke.kem.encSize);
  return new Uint8Array(await hpke.open({ recipientKey: keyPair, enc, info: encoder.encode(context) }, bytes.subarray(enc.length)));
}
