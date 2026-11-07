// The signed-in account (#account-keys) and its key pair. The wrapping key is kept in IndexedDB from sign-in to
// sign-out.

import { del, get, set } from "../../vendor/idb-keyval-6.3.0/index.js";
import { unwrapKeyPair } from "../lib/account-crypto.js";
import { readJson } from "./page-data.js";

const WRAPPING_KEY_ID = "wrapping-key";
export const storeWrappingKey = (wrappingKey) => set(WRAPPING_KEY_ID, wrappingKey);
export const loadWrappingKey = () => get(WRAPPING_KEY_ID);
export const deleteWrappingKey = () => del(WRAPPING_KEY_ID).catch(() => {});

/** Null when signed out. Read on each call, as an htmx swap can replace it. */
export const signedInAccount = () => readJson("account-keys");

let keyPairCache = null; // { wrappedPrivateKey, keyPair: Promise }

/** Null when this browser has no wrapping key, or one from another password. */
export function loadKeyPair() {
  const account = signedInAccount();
  if (!account?.wrappedPrivateKey) return Promise.resolve(null);
  if (keyPairCache?.wrappedPrivateKey !== account.wrappedPrivateKey) {
    const keyPair = loadWrappingKey()
      .then((wrappingKey) => (wrappingKey ? unwrapKeyPair(account, wrappingKey) : null))
      .catch(() => null);
    keyPairCache = { wrappedPrivateKey: account.wrappedPrivateKey, keyPair };
  }
  return keyPairCache.keyPair;
}
