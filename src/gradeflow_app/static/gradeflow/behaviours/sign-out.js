// Signing out forgets the wrapping key. Browsers can clear IndexedDB during a session (Safari after 7 days without a
// visit), and only signing in derives the key again, so a browser that lost it signs out. One without IndexedDB stays
// signed in, without student IDs.

import { deleteWrappingKey, loadKeyPair, loadWrappingKey, signedInAccount } from "../services/account.js";
import { send } from "../services/requests.js";

async function signOutWithoutKeyPair() {
  const keyPair = await loadKeyPair();
  const hasIndexedDb = await loadWrappingKey().then(
    () => true,
    () => false,
  );
  if (keyPair || !signedInAccount()?.wrappedPrivateKey || !hasIndexedDb) return;
  await send("/accounts/logout/");
  location.assign(`/accounts/login/?${new URLSearchParams({ next: location.pathname + location.search })}`);
}

export function start() {
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
  signOutWithoutKeyPair();
}
