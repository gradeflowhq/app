// Form fields and table rows that do more than the browser does.

// A table row with `data-href` follows its first link when clicked anywhere.
document.addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-href]");
  if (row && !event.target.closest("a, button, input, label, select")) row.querySelector("a[href]")?.click();
});
// An insert button adds its value where the cursor was in the last feedback box used.
let lastBox;
document.addEventListener("focusin", (event) => {
  if (event.target.closest("[data-inserts]") && event.target.matches("textarea")) lastBox = event.target;
});
document.addEventListener("click", (event) => {
  const insert = event.target.closest("[data-insert]");
  const box = insert && (insert.closest("[data-inserts]").contains(lastBox) ? lastBox : insert.closest("[data-inserts]").querySelector("textarea"));
  if (!box) return;
  const at = box.selectionStart ?? box.value.length;
  box.setRangeText(insert.dataset.insert, at, box.selectionEnd ?? at, "end");
  box.focus();
  box.dispatchEvent(new Event("change", { bubbles: true }));
});
// A quick pick beside a field sets the field, as typing it would.
document.addEventListener("click", (event) => {
  const pick = event.target.closest("[data-preset]");
  const input = pick && document.getElementById(pick.closest("[data-presets]").dataset.presets);
  if (!input) return;
  input.value = pick.dataset.preset;
  for (const other of pick.parentElement.children) other.setAttribute("aria-pressed", String(other === pick));
  input.dispatchEvent(new Event("change", { bubbles: true }));
});

// A morph keeps what was typed in a form, so a button with `data-resets` empties its form once its request succeeds.
document.addEventListener("htmx:finally:request", ({ detail: { ctx } }) => {
  if (ctx.response?.status < 400 && ctx.request.submitter?.hasAttribute("data-resets")) ctx.request.form?.reset();
});

// A field with `data-visible-when` shows only while the fields it names have the values it wants, or one of a list
// of them, as the server decided when it rendered them (schemaforms.HintedField).
const currentValue = (control) => (control?.type === "checkbox" ? control.checked : control?.value);
const same = (value, wanted) => (typeof wanted === "boolean" ? Boolean(value) === wanted : String(value) === String(wanted));
const wants = (form, [name, wanted]) => {
  const value = currentValue(form.elements.namedItem(name));
  return [wanted].flat().some((option) => same(value, option));
};
document.addEventListener("change", ({ target: { form } }) => {
  for (const field of form?.querySelectorAll("[data-visible-when]") ?? []) {
    field.hidden = !Object.entries(JSON.parse(field.dataset.visibleWhen)).every((condition) => wants(form, condition));
  }
});
