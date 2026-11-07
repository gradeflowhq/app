// A dialog that opens as soon as it appears, such as one shown again with errors. Closing it drops the errors, so
// opening it again starts afresh.

import { openDialog } from "../services/dialogs.js";

export const openOnLoad = () => ({
  init() {
    openDialog(this.$el);
    this.$el.addEventListener("close", () => this.clearErrors(), { once: true });
  },
  clearErrors() {
    for (const alert of this.$el.querySelectorAll("form [role=alert]")) alert.remove();
    for (const errors of this.$el.querySelectorAll('[id$="_error"]')) errors.replaceChildren();
    for (const field of this.$el.querySelectorAll("[aria-invalid]")) field.removeAttribute("aria-invalid");
  },
});
