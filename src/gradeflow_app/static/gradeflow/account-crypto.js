// Account cryptography. Argon2id and HKDF turn the password into an authentication key, which the server stores in
// place of the password, and a wrapping key, which never leaves the browser and wraps the account's X25519 private key.
// HPKE (RFC 9180) encrypts data, such as course keys, for a public key.

import { Aes128Gcm, CipherSuite, DhkemX25519HkdfSha256, HkdfSha256 } from "../vendor/hpke-core-1.9.0/hpke.min.js";
import { argon2idAsync } from "../vendor/noble-hashes-2.4.0/argon2.min.js";

const ARGON2_OPTIONS = { t: 3, m: 64 * 1024, p: 1, dkLen: 32, asyncTick: 10 };
const KDF_SALT_SIZE = 16;

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

const importPublicKey = (publicKey) => crypto.subtle.importKey("raw", Uint8Array.fromBase64(publicKey), "X25519", true, []);

/** Base64 of the IV, then the private key in PKCS #8, wrapped with AES-GCM. */
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

/** The key pair, with a private key that can't be exported. Rejects when `wrappingKey` doesn't unwrap it. */
export async function unwrapKeyPair({ publicKey, wrappedPrivateKey }, wrappingKey) {
  return {
    privateKey: await unwrapPrivateKey(wrappedPrivateKey, wrappingKey, false),
    publicKey: await importPublicKey(publicKey),
  };
}

/** `bytes` encrypted with HPKE for `publicKey`, bound to `context`: the encapsulated key, then the ciphertext. */
export async function encryptForPublicKey(publicKey, bytes, context) {
  const { enc, ct } = await hpke.seal({ recipientPublicKey: await importPublicKey(publicKey), info: encoder.encode(context) }, bytes);
  return new Uint8Array([...new Uint8Array(enc), ...new Uint8Array(ct)]).toBase64();
}

/** What `encryptForPublicKey` encrypted for `keyPair`'s public key with the same `context`. Rejects otherwise. */
export async function decryptWithKeyPair(keyPair, encrypted, context) {
  const bytes = Uint8Array.fromBase64(encrypted);
  const enc = bytes.subarray(0, hpke.kem.encSize);
  return new Uint8Array(await hpke.open({ recipientKey: keyPair, enc, info: encoder.encode(context) }, bytes.subarray(enc.length)));
}
