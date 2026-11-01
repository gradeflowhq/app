// Toasts, shown in base.html. The app's messages arrive with a page (#messages) or in the X-Messages header of an
// in-page response (htmx.js).

import Alpine from "../vendor/alpinejs-csp-3.17.4/module.esm.min.js";

const SHOWN_MS = 6000;

Alpine.store("toasts", {
  shown: [],
  count: 0,
  report(message, level = "error") {
    const toast = { id: ++this.count, message, ok: level !== "error", style: level === "error" ? "alert-error" : "alert-neutral" };
    this.shown = [toast];
    setTimeout(() => (this.shown = this.shown.filter((shown) => shown.id !== toast.id)), SHOWN_MS);
  },
  clear() {
    this.shown = [];
  },
});

/** Shows `message` as a toast; `level` is "success" or the default, "error". */
export const report = (message, level) => Alpine.store("toasts").report(message, level);

function reportPageMessages() {
  const messages = document.getElementById("messages");
  messages?.remove();
  for (const { text, level } of JSON.parse(messages?.textContent ?? "[]")) report(text, level);
}
reportPageMessages();
document.addEventListener("htmx:after:settle", reportPageMessages);
// Toasts belong to the page that raised them: going to another page clears them before its own arrive.
for (const name of ["htmx:after:history:push", "htmx:before:history:restore"]) {
  document.addEventListener(name, () => Alpine.store("toasts").clear());
}
