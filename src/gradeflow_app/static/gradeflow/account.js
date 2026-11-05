// The signed-in account (#account-keys) and its key pair. The wrapping key is kept in IndexedDB from sign-in to
// sign-out.

import { del, get, set } from "../vendor/idb-keyval-6.3.0/index.js";
import { unwrapKeyPair } from "./account-crypto.js";
import { send } from "./requests.js";

const WRAPPING_KEY_ID = "wrapping-key";
export const storeWrappingKey = (wrappingKey) => set(WRAPPING_KEY_ID, wrappingKey);
export const loadWrappingKey = () => get(WRAPPING_KEY_ID);
const deleteWrappingKey = () => del(WRAPPING_KEY_ID).catch(() => {});

/** Null when signed out. Read on each call, as an htmx swap can replace it. */
export const signedInAccount = () => JSON.parse(document.getElementById("account-keys")?.textContent ?? "null");

let keyPairCache = null; // { wrappedPrivateKey, keyPair: Promise }

/** Null when this browser has no wrapping key, or one from another password. */
export function loadKeyPair() {
  const account = signedInAccount();
  if (!account?.wrappedPrivateKey) return Promise.resolve(null);
  if (keyPairCache?.wrappedPrivateKey !== account.wrappedPrivateKey) {
    const keyPair = loadWrappingKey().then((wrappingKey) => wrappingKey && unwrapKeyPair(account, wrappingKey).catch(() => null));
    keyPairCache = { wrappedPrivateKey: account.wrappedPrivateKey, keyPair: keyPair.then((pair) => pair ?? null, () => null) };
  }
  return keyPairCache.keyPair;
}

document.addEventListener(
  "submit",
  async (event) => {
    const form = event.target;
    if (!form.getAttribute("action")?.endsWith("/accounts/logout/")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    await deleteWrappingKey();
    form.submit();
  },
  true,
);

// Browsers can clear IndexedDB during a session (Safari after 7 days without a visit), and only signing in again
// derives the wrapping key. A browser without IndexedDB stays signed in, without student IDs.
loadKeyPair().then(async (keyPair) => {
  const hasIndexedDb = await loadWrappingKey().then(() => true, () => false);
  if (keyPair || !signedInAccount()?.wrappedPrivateKey || !hasIndexedDb) return;
  await send("/accounts/logout/");
  location.assign(`/accounts/login/?${new URLSearchParams({ next: location.pathname + location.search })}`);
});
