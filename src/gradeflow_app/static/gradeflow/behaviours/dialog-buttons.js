// Buttons that open, close or confirm through a dialog, and menu items.

import { confirmAction, openDialog } from "../services/dialogs.js";

const SKELETON =
  '<div class="grid gap-3 p-6"><div class="skeleton h-7 w-1/2"></div><div class="skeleton h-40 w-full"></div></div>';

const confirmed = new WeakSet();

export function start() {
  // A `data-dialog` button opens the dialog it names. One that also loads the dialog's content shows a skeleton until
  // it arrives, never what was there before.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-dialog]");
    if (!button) return;
    const loaded =
      button.matches(":is([hx-get], [hx-post])[hx-target]") && document.querySelector(button.getAttribute("hx-target"));
    if (loaded) loaded.innerHTML = SKELETON;
    openDialog(document.getElementById(button.dataset.dialog));
  });
  // A `data-close` button closes its dialog as Esc does, so a `cancel` listener can keep it open.
  document.addEventListener("click", (event) =>
    event.target.closest("[data-close]")?.closest("dialog")?.requestClose(),
  );
  // A `data-close-on-success` form closes its dialog once it's sent. Its fields' own requests leave it open.
  document.addEventListener("htmx:after:request", (event) => {
    const { sourceElement, response } = event.detail.ctx;
    if (response?.status < 400 && sourceElement?.matches("[data-close-on-success]"))
      sourceElement.closest("dialog")?.close();
  });
  // A menu item closes its menu and gives up focus, unless it opens a dialog, which takes focus.
  document.addEventListener("click", (event) => {
    const item = event.target.closest("[popover] .menu :is(button, a)");
    if (!item) return;
    item.closest("[popover]").hidePopover();
    if (!item.matches("[data-dialog]")) document.activeElement?.blur();
  });
  // A `data-confirm` button asks first, and is clicked again once confirmed.
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
}
