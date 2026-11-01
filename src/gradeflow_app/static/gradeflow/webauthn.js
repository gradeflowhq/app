// Module dependencies run in order, including when htmx inserts the passkey page's scripts.
import "../mfa/js/webauthn-json.js";
import "../mfa/js/webauthn.js";
import "../account/js/onload.js";
import { report } from "./toasts.js";

document.addEventListener("allauth.error", (event) => {
  event.preventDefault();
  const name = event.detail.exception.name;
  if (name === "NotAllowedError") report("Passkey request cancelled. Try again.", "info");
  else if (name === "InvalidStateError") report("This passkey is already registered. Use a different device or security key.");
  else report("Couldn't use the passkey. Try again.");
});
