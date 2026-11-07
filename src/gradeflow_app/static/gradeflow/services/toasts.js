// Toasts (base.html): one at a time, a new one replacing it.

import Alpine from "../../vendor/alpinejs-csp-3.17.4/module.esm.min.js";

const VISIBLE_MS = 6000;
let hideTimer;

/** The `toasts` store. `visible` is a list, so the template never reads a toast that's gone. */
export const toastStore = {
  visible: [],
  show(message, level = "error") {
    this.visible = [
      { message, isSuccess: level === "success", alertClass: level === "error" ? "alert-error" : "alert-neutral" },
    ];
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => this.clear(), VISIBLE_MS);
  },
  clear() {
    this.visible = [];
  },
};

/** Shows `message`; `level` is a message level, "error" by default. */
export const showToast = (message, level) => Alpine.store("toasts").show(message, level);

export const clearToasts = () => Alpine.store("toasts").clear();
