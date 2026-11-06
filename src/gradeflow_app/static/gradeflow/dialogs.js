// Dialogs and menus: opening, closing and confirming.

// A dialog shows a skeleton until what a button loads into it arrives, never what it showed before.
document.addEventListener("click", (event) => {
  const openButton = event.target.closest("[data-dialog]:is([hx-get], [hx-post])[hx-target]");
  const target = openButton && document.querySelector(openButton.getAttribute("hx-target"));
  if (target) target.innerHTML = '<div class="grid gap-3 p-6"><div class="skeleton h-7 w-1/2"></div><div class="skeleton h-40 w-full"></div></div>';
});
// A `data-dialog` button opens the dialog it names; an `openOnLoad` dialog opens as soon as it appears. Focus goes to its
// first field, or else the dialog itself, never its close button.
function openDialog(dialog) {
  dialog.showModal();
  (dialog.querySelector("[autofocus], input:not([type=hidden]), select, textarea") ?? dialog.querySelector(".modal-box"))?.focus();
}
document.addEventListener("click", (event) => {
  const openButton = event.target.closest("[data-dialog]");
  if (openButton) openDialog(document.getElementById(openButton.dataset.dialog));
});
// Closing one shown with errors drops them, so opening it again starts afresh.
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
// Choosing an item of a menu closes it, though its page may keep focus there after a swap; one that opens a dialog
// leaves focus to the dialog.
document.addEventListener("click", (event) => {
  const item = event.target.closest("[popover] .menu :is(button, a)");
  item?.closest("[popover]").hidePopover();
  if (item && !item.matches("[data-dialog]")) document.activeElement?.blur();
});

// A `data-close` button closes the dialog around it, as Esc does: a `cancel` listener can keep it open.
document.addEventListener("click", (event) => event.target.closest("[data-close]")?.closest("dialog")?.requestClose());
// A `data-close-on-success` form closes its dialog once it is sent: its fields' own requests, such as a select
// that reloads the dialog, leave it open.
document.addEventListener("htmx:after:request", (event) => {
  const { sourceElement, response } = event.detail.ctx;
  if (response?.status < 400 && sourceElement?.matches("[data-close-on-success]")) sourceElement.closest("dialog")?.close();
});

/** Asks `question` in the confirm dialog (base.html), with `detail` under it and `buttonLabel` on its button;
 * resolves whether it was confirmed. */
export function confirmAction(question, detail, buttonLabel) {
  const dialog = document.getElementById("confirm");
  const detailElement = dialog.querySelector("[data-detail]");
  dialog.querySelector("h2").textContent = question;
  detailElement.textContent = detail;
  detailElement.hidden = !detail;
  dialog.querySelector("[value=confirm]").textContent = buttonLabel;
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => (dialog.onclose = () => resolve(dialog.returnValue === "confirm")));
}

// A `data-confirm` button asks first; confirming clicks it again.
const confirmed = new WeakSet();
document.addEventListener(
  "click",
  async (event) => {
    const button = event.target.closest("[data-confirm]");
    if (!button || confirmed.delete(button)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const { confirm, confirmDetail = "", confirmButton = button.textContent.trim() } = button.dataset;
    if (!(await confirmAction(confirm, confirmDetail, confirmButton))) return;
    confirmed.add(button);
    button.click();
  },
  true,
);
