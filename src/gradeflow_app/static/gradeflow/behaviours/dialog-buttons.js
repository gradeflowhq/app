// Buttons that open, close or confirm through a dialog, and menu items.

import { confirmAction, openDialog } from "../services/dialogs.js";

const SKELETON =
  '<div class="grid gap-3 p-6"><div class="skeleton h-7 w-1/2"></div><div class="skeleton h-40 w-full"></div></div>';

const confirmed = new WeakSet();
const focusAfterSwap = new WeakMap();

/** The button that opens the popover `element` is in. */
function popoverButton(element) {
  const popover = element.closest("[popover]");
  return popover && document.querySelector(`[popovertarget="${popover.id}"]`);
}

export function start() {
  // A `data-dialog` button opens the dialog it names. One that also loads the dialog's content shows a skeleton until
  // it arrives, never what was there before. Closing the dialog gives focus back to the button, or to the popover's
  // button for one in a popover.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-dialog]");
    if (!button) return;
    const loaded =
      button.matches(":is([hx-get], [hx-post])[hx-target]") && document.querySelector(button.getAttribute("hx-target"));
    if (loaded) loaded.innerHTML = SKELETON;
    const menuButton = popoverButton(button);
    menuButton?.focus();
    openDialog(document.getElementById(button.dataset.dialog), menuButton ?? button);
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
  // A `data-focus-after-swap` form gives focus to the element it names once what it loaded is in. The selector is read
  // before the swap, which can replace the form.
  document.addEventListener("htmx:before:swap", (event) => {
    const selector = event.detail.ctx.sourceElement?.dataset?.focusAfterSwap;
    if (selector) focusAfterSwap.set(event.detail.ctx, selector);
  });
  document.addEventListener("htmx:after:swap", (event) => {
    const selector = focusAfterSwap.get(event.detail.ctx);
    if (selector) document.querySelector(selector)?.focus();
  });
  // A menu item closes its menu and gives up focus, unless it opens a dialog, which takes focus.
  document.addEventListener("click", (event) => {
    const item = event.target.closest("[popover] .menu :is(button, a)");
    if (!item) return;
    item.closest("[popover]").hidePopover();
    if (!item.matches("[data-dialog]")) document.activeElement?.blur();
  });
  // A `data-confirm` button asks first, and is clicked again once confirmed. One in a popover gives focus to the
  // popover's button, so the confirm dialog returns it there.
  document.addEventListener(
    "click",
    async (event) => {
      const button = event.target.closest("[data-confirm]");
      if (!button || confirmed.delete(button)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const { confirm, confirmDetail = "", confirmButton = button.textContent.trim() } = button.dataset;
      popoverButton(button)?.focus();
      if (!(await confirmAction(confirm, confirmDetail, confirmButton))) return;
      confirmed.add(button);
      button.click();
    },
    true,
  );
}
