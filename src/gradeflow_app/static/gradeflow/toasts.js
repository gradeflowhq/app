// Toasts, shown in base.html. The app's messages arrive with a page (#messages) or in the X-Messages header of an
// in-page response (htmx.js).

import Alpine from "../vendor/alpinejs-csp-3.17.4/module.esm.min.js";

const VISIBLE_MS = 6000;
let hideTimer;

// At most one toast, which a new one replaces. A list, so the toast's fields are never read once it's gone.
Alpine.store("toasts", {
  visible: [],
  show(message, level = "error") {
    this.visible = [{ message, isSuccess: level === "success", alertClass: level === "error" ? "alert-error" : "alert-neutral" }];
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => this.clear(), VISIBLE_MS);
  },
  clear() {
    this.visible = [];
  },
});

/** Shows `message` as a toast; `level` is a message level, by default "error". */
export const showToast = (message, level) => Alpine.store("toasts").show(message, level);

function showPageMessages() {
  const messages = document.getElementById("messages");
  messages?.remove();
  for (const { text, level } of JSON.parse(messages?.textContent ?? "[]")) showToast(text, level);
}
showPageMessages();
document.addEventListener("htmx:after:settle", showPageMessages);
// Toasts belong to the page that raised them: going to another page clears them before its own arrive.
for (const name of ["htmx:after:history:push", "htmx:before:history:restore"]) {
  document.addEventListener(name, () => Alpine.store("toasts").clear());
}
