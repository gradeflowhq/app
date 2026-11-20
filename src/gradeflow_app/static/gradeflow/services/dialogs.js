// Opening dialogs, and the confirm dialog (base.html).

/** `element` if it's a button or link, or else the first one inside it. */
const focusable = (element) => (element?.matches("button, a") ? element : element?.querySelector("button, a"));

/** What to give focus back to: `opener`, or what loads the same content once a swap has replaced `opener`. */
function returnFocusTarget(opener) {
  if (!opener || opener === document.body) return null;
  if (opener.isConnected) return focusable(opener);
  const url = opener.closest("[hx-get]")?.getAttribute("hx-get");
  return url && focusable(document.querySelector(`[hx-get="${CSS.escape(url)}"]`));
}

/** Opens `dialog` with focus on its first field, or else the dialog itself, never its close button. Closing it
 * resets its forms, and gives focus back to `opener`, or to its replacement if a swap made a new one. */
export function openDialog(dialog, opener = document.activeElement) {
  dialog.showModal();
  (
    dialog.querySelector("[autofocus], input:not([type=hidden]), select, textarea") ??
    dialog.querySelector(".modal-box")
  )?.focus();
  dialog.addEventListener(
    "close",
    () => {
      for (const form of dialog.querySelectorAll("form")) form.reset();
      const { activeElement } = document;
      if (activeElement === document.body || dialog.contains(activeElement)) returnFocusTarget(opener)?.focus();
    },
    { once: true },
  );
}

/** Asks `question`, with `detail` under it and `buttonLabel` on its button; resolves whether it was confirmed. */
export function confirmAction(question, detail, buttonLabel) {
  const dialog = document.getElementById("confirm");
  const detailElement = dialog.querySelector("[data-detail]");
  dialog.querySelector("h2").textContent = question;
  detailElement.textContent = detail;
  detailElement.hidden = !detail;
  dialog.querySelector("[value=confirm]").textContent = buttonLabel;
  dialog.returnValue = "";
  openDialog(dialog);
  return new Promise((resolve) => {
    dialog.onclose = () => resolve(dialog.returnValue === "confirm");
  });
}
