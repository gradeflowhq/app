// Toasts, shown in base.html. The app's messages arrive with a page (#messages) or in the X-Messages header of an
// in-page response (htmx.js).

import Alpine from "../vendor/alpinejs-csp-3.17.4/module.esm.min.js";

const VISIBLE_MS = 6000;

Alpine.store("toasts", {
  visible: [],
  count: 0,
  show(message, level = "error") {
    const toast = { id: ++this.count, message, isSuccess: level === "success", alertClass: level === "error" ? "alert-error" : "alert-neutral" };
    this.visible = [toast];
    setTimeout(() => (this.visible = this.visible.filter((other) => other.id !== toast.id)), VISIBLE_MS);
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
