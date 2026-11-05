// allauth's password forms send the authentication key derived from a password (account-crypto.js), never the
// password. Forms that set a password also send its KDF salt and the private key wrapped with its wrapping key: the
// account's key pair, rewrapped, when the current password is given; a new key pair otherwise.

import { signedInAccount, storeWrappingKey } from "./account.js";
import { deriveKeysFromPassword, generateKdfSalt, generateKeyPair, rewrapPrivateKey } from "./account-crypto.js";
import { send } from "./requests.js";
import { showToast } from "./toasts.js";

const PASSWORD_FIELDS = ["password", "oldpassword", "password1", "password2"];

async function fetchKdfSalt(form) {
  if (!form.elements.login) return signedInAccount().kdfSalt; // reauthentication
  const response = await send("/accounts/kdf-salt/", { values: { email: form.elements.login.value.trim() } });
  return (await response.json()).kdfSalt;
}

function showFieldError(field, message) {
  field.setCustomValidity(message);
  field.reportValidity();
  field.addEventListener("input", () => field.setCustomValidity(""), { once: true });
  return null;
}

/**
 * The fields to send in place of the form's passwords, and the wrapping key to store once the server accepts them;
 * null after showing an error the browser can find itself.
 */
async function derivePasswordFields(form) {
  const { password, oldpassword, password1, password2 } = form.elements;
  const account = signedInAccount();
  if (password) {
    const { authKey, wrappingKey } = await deriveKeysFromPassword(password.value, await fetchKdfSalt(form));
    // Reauthentication keeps the wrapping key this browser already has.
    return { fields: { password: authKey }, wrappingKey: account ? null : wrappingKey };
  }
  if (password2 && password2.value !== password1.value) return showFieldError(password2, "The passwords don't match.");
  const kdfSalt = generateKdfSalt();
  const { authKey, wrappingKey } = await deriveKeysFromPassword(password1.value, kdfSalt);
  const fields = { password1: authKey, password2: authKey, kdf_salt: kdfSalt };
  if (oldpassword) {
    const old = await deriveKeysFromPassword(oldpassword.value, account.kdfSalt);
    const wrappedPrivateKey = await rewrapPrivateKey(account.wrappedPrivateKey, old.wrappingKey, wrappingKey).catch(() => null);
    if (!wrappedPrivateKey) return showFieldError(oldpassword, "Wrong password.");
    return { fields: { ...fields, oldpassword: old.authKey, wrapped_private_key: wrappedPrivateKey }, wrappingKey };
  }
  const { publicKey, wrappedPrivateKey } = await generateKeyPair(wrappingKey);
  return { fields: { ...fields, public_key: publicKey, wrapped_private_key: wrappedPrivateKey }, wrappingKey };
}

// The buttons of these forms render disabled, so no form can submit a password before this script can replace it.
const enableSubmitButtons = () => document.querySelectorAll("[data-disabled-until-script]").forEach((button) => (button.disabled = false));
enableSubmitButtons();
document.addEventListener("htmx:after:settle", enableSubmitButtons);

const hasPasswordFields = (form) => PASSWORD_FIELDS.some((name) => form.elements[name]?.type === "password");

const derivedFieldsByForm = new WeakMap();
let formBeingResubmitted = null;

document.addEventListener(
  "submit",
  async (event) => {
    const form = event.target;
    if (form === formBeingResubmitted || !hasPasswordFields(form)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    derivedFieldsByForm.delete(form);
    const button = event.submitter;
    const stopSpinner = () => {
      button?.removeAttribute("aria-busy");
      button?.removeAttribute("data-spinner");
    };
    button?.setAttribute("aria-busy", "true");
    button?.setAttribute("data-spinner", "");
    try {
      const derived = await derivePasswordFields(form);
      if (!derived) return stopSpinner();
      // Without IndexedDB the browser still signs in, without student IDs.
      if (derived.wrappingKey) await storeWrappingKey(derived.wrappingKey).catch(() => {});
      derivedFieldsByForm.set(form, derived.fields);
      formBeingResubmitted = form;
      form.requestSubmit(button);
    } catch {
      showToast("That didn't work. Try again in a moment.");
      stopSpinner();
    } finally {
      formBeingResubmitted = null;
    }
  },
  true,
);

// Both the browser and htmx build the request body with FormData, which fires this event.
document.addEventListener(
  "formdata",
  (event) => {
    if (!hasPasswordFields(event.target)) return;
    for (const name of PASSWORD_FIELDS) event.formData.delete(name);
    for (const [name, value] of Object.entries(derivedFieldsByForm.get(event.target) ?? {})) event.formData.set(name, value);
  },
  true,
);
