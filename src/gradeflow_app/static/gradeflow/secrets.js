// The account's secrets, such as the Canvas token, encrypted for its public key (accounts/models.py Secret).

import { loadKeyPair, signedInAccount } from "./account.js";
import { decryptWithKeyPair, encryptForPublicKey } from "./account-crypto.js";
import { send } from "./requests.js";

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const secretContext = (name, userId) => `gradeflow:secret:${name}:${userId}`;

/** Null when there is none, or this browser can't load the key pair. */
export async function readSecret(name) {
  const encryptedValue = signedInAccount()?.encryptedSecrets[name];
  const keyPair = encryptedValue && (await loadKeyPair());
  if (!keyPair) return null;
  try {
    const context = secretContext(name, signedInAccount().userId);
    return JSON.parse(decoder.decode(await decryptWithKeyPair(keyPair, encryptedValue, context)));
  } catch {
    return null;
  }
}

export async function saveSecret(name, value) {
  const { publicKey, userId } = signedInAccount();
  const encryptedValue = await encryptForPublicKey(publicKey, encoder.encode(JSON.stringify(value)), secretContext(name, userId));
  await send(`/settings/secrets/${name}/`, { values: { encrypted_value: encryptedValue } });
}

export async function deleteSecret(name) {
  await send(`/settings/secrets/${name}/`, { method: "DELETE" });
}
