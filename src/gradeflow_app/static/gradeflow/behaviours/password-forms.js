// allauth's password forms send the authentication key instead of the password (lib/account-crypto.js). Forms that
// set a password also send its KDF salt and the private key wrapped with its wrapping key: the same key pair, given
// the current password or the recovery key, or else a new one.

import {
  deriveKeysFromPassword,
  generateKdfSalt,
  generateKeyPair,
  importRecoveryKey,
  rewrapPrivateKey,
} from "../lib/account-crypto.js";
import { signedInAccount, storeWrappingKey } from "../services/account.js";
import { send } from "../services/requests.js";
import { showToast } from "../services/toasts.js";
import { markBusy } from "./busy-buttons.js";

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

/** The fields to send in place of the passwords, and the wrapping key to store; null after showing an error. */
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
    const wrappedPrivateKey = await rewrapPrivateKey(account.wrappedPrivateKey, old.wrappingKey, wrappingKey).catch(
      () => null,
    );
    if (!wrappedPrivateKey) return showFieldError(oldpassword, "Wrong password.");
    return { fields: { ...fields, oldpassword: old.authKey, wrapped_private_key: wrappedPrivateKey }, wrappingKey };
  }
  const recoveryKeyField = form.elements.recovery_key;
  if (recoveryKeyField?.value.trim()) {
    const { publicKey, recoveryWrappedPrivateKey } = recoveryKeyField.dataset;
    const recoveryWrappingKey = await importRecoveryKey(recoveryKeyField.value).catch(() => null);
    const wrappedPrivateKey =
      recoveryWrappingKey &&
      (await rewrapPrivateKey(recoveryWrappedPrivateKey, recoveryWrappingKey, wrappingKey).catch(() => null));
    if (!wrappedPrivateKey) return showFieldError(recoveryKeyField, "Wrong recovery key.");
    return { fields: { ...fields, public_key: publicKey, wrapped_private_key: wrappedPrivateKey }, wrappingKey };
  }
  const { publicKey, wrappedPrivateKey } = await generateKeyPair(wrappingKey);
  return { fields: { ...fields, public_key: publicKey, wrapped_private_key: wrappedPrivateKey }, wrappingKey };
}

// The buttons render disabled, so no form can send a password before this script loads.
const enableSubmitButtons = () => {
  for (const button of document.querySelectorAll("[data-disabled-until-script]")) button.disabled = false;
};

const hasPasswordFields = (form) => PASSWORD_FIELDS.some((name) => form.elements[name]?.type === "password");

const derivedFieldsByForm = new WeakMap();
let formBeingResubmitted = null;

/** Derives the fields to send, then sends the form again with them; the `formdata` listener puts them in. */
async function submitDerived(event) {
  const form = event.target;
  if (form === formBeingResubmitted || !hasPasswordFields(form)) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  derivedFieldsByForm.delete(form);
  const button = event.submitter;
  const endBusy = button ? markBusy(button) : () => {};
  try {
    const derived = await derivePasswordFields(form);
    if (!derived) return endBusy();
    // Without IndexedDB the browser still signs in, without student IDs.
    if (derived.wrappingKey) await storeWrappingKey(derived.wrappingKey).catch(() => {});
    derivedFieldsByForm.set(form, derived.fields);
    formBeingResubmitted = form;
    form.requestSubmit(button);
  } catch {
    showToast("That didn't work. Try again in a moment.");
    endBusy();
  } finally {
    formBeingResubmitted = null;
  }
}

/** Both the browser and htmx build the request body with FormData, which fires this event. */
function replacePasswords(event) {
  if (!hasPasswordFields(event.target)) return;
  for (const name of PASSWORD_FIELDS) event.formData.delete(name);
  for (const [name, value] of Object.entries(derivedFieldsByForm.get(event.target) ?? {}))
    event.formData.set(name, value);
}

export function start() {
  enableSubmitButtons();
  document.addEventListener("htmx:after:settle", enableSubmitButtons);
  document.addEventListener("submit", submitDerived, true);
  document.addEventListener("formdata", replacePasswords, true);
}
