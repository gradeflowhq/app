// Opening dialogs, and the confirm dialog (base.html).

/** Opens `dialog` with focus on its first field, or else the dialog itself, never its close button. */
export function openDialog(dialog) {
  dialog.showModal();
  (
    dialog.querySelector("[autofocus], input:not([type=hidden]), select, textarea") ??
    dialog.querySelector(".modal-box")
  )?.focus();
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
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.onclose = () => resolve(dialog.returnValue === "confirm");
  });
}
