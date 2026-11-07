// Module dependencies run in order, including when htmx inserts the passkey page's scripts.
import "../mfa/js/webauthn-json.js";
import "../mfa/js/webauthn.js";
import "../account/js/onload.js";
import { showToast } from "./services/toasts.js";

document.addEventListener("allauth.error", (event) => {
  event.preventDefault();
  const name = event.detail.exception.name;
  if (name === "NotAllowedError") showToast("Passkey request cancelled. Try again.", "info");
  else if (name === "InvalidStateError")
    showToast("This passkey is already registered. Use a different device or security key.");
  else showToast("Couldn't use the passkey. Try again.");
});
