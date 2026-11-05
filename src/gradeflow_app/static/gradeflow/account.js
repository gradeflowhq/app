// The signed-in account (#account-keys) and its key pair. The wrapping key derived from the password stays in this
// browser's IndexedDB from sign-in until sign-out.

import { del, get, set } from "../vendor/idb-keyval-6.3.0/index.js";
import { unwrapKeyPair } from "./account-crypto.js";
import { send } from "./requests.js";

const WRAPPING_KEY_ID = "wrapping-key";
export const storeWrappingKey = (wrappingKey) => set(WRAPPING_KEY_ID, wrappingKey);
const deleteWrappingKey = () => del(WRAPPING_KEY_ID).catch(() => {});

/** The signed-in account, or null when signed out. Read on each call, since swapping the page can replace it. */
export const signedInAccount = () => JSON.parse(document.getElementById("account-keys")?.textContent ?? "null");

let keyPairCache = null; // { wrappedPrivateKey, keyPair: Promise }

/** The signed-in account's key pair, or null when this browser has no wrapping key that unwraps it. */
export function loadKeyPair() {
  const account = signedInAccount();
  if (!account?.wrappedPrivateKey) return Promise.resolve(null);
  if (keyPairCache?.wrappedPrivateKey !== account.wrappedPrivateKey) {
    const keyPair = get(WRAPPING_KEY_ID).then((wrappingKey) => wrappingKey && unwrapKeyPair(account, wrappingKey).catch(() => null));
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

// Browsers can clear IndexedDB while the session lasts (Safari after 7 days without a visit). Signing in again derives
// the wrapping key again. A browser without IndexedDB stays signed in, without student IDs.
loadKeyPair().then(async (keyPair) => {
  const hasIndexedDb = await get(WRAPPING_KEY_ID).then(() => true, () => false);
  if (keyPair || !signedInAccount()?.wrappedPrivateKey || !hasIndexedDb) return;
  await send("/accounts/logout/");
  location.assign(`/accounts/login/?${new URLSearchParams({ next: location.pathname + location.search })}`);
});
