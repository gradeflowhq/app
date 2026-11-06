// Form fields and table rows that do more than the browser does.

// A table row with `data-href` follows its link when clicked.
document.addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-href]");
  if (row && !event.target.closest("a")) row.querySelector("a").click();
});
// An insert button adds its value where the cursor was in the last feedback field used.
let lastFeedbackField;
document.addEventListener("focusin", (event) => {
  if (event.target.closest("[data-feedback-fields]") && event.target.matches("textarea")) lastFeedbackField = event.target;
});
document.addEventListener("click", (event) => {
  const insert = event.target.closest("[data-insert]");
  const field = insert && (insert.closest("[data-feedback-fields]").contains(lastFeedbackField) ? lastFeedbackField : insert.closest("[data-feedback-fields]").querySelector("textarea"));
  if (!field) return;
  const cursor = field.selectionStart ?? field.value.length;
  field.setRangeText(insert.dataset.insert, cursor, field.selectionEnd ?? cursor, "end");
  field.focus();
  field.dispatchEvent(new Event("change", { bubbles: true }));
});
// A preset beside a field sets the field, as typing it would.
document.addEventListener("click", (event) => {
  const preset = event.target.closest("[data-preset]");
  const input = preset && document.getElementById(preset.closest("[data-presets]").dataset.presets);
  if (!input) return;
  input.value = preset.dataset.preset;
  for (const other of preset.parentElement.children) other.setAttribute("aria-pressed", String(other === preset));
  input.dispatchEvent(new Event("change", { bubbles: true }));
});

// A morph keeps what was typed in a form, so a button with `data-resets` empties its form once its request succeeds.
document.addEventListener("htmx:finally:request", ({ detail: { ctx } }) => {
  if (ctx.response?.status < 400 && ctx.request.submitter?.hasAttribute("data-resets")) ctx.request.form?.reset();
});

// A field with `data-visible-when` shows only while each field it names has one of the values listed for it, as the
// server decided when it rendered them (schemaforms.HintedField).
const currentValue = (control) => (control?.type === "checkbox" ? control.checked : control?.value);
const same = (value, wanted) => (typeof wanted === "boolean" ? Boolean(value) === wanted : String(value) === String(wanted));
const meetsCondition = (form, [name, wanted]) => {
  const value = currentValue(form.elements.namedItem(name));
  return wanted.some((option) => same(value, option));
};
document.addEventListener("change", ({ target: { form } }) => {
  for (const field of form?.querySelectorAll("[data-visible-when]") ?? []) {
    field.hidden = !Object.entries(JSON.parse(field.dataset.visibleWhen)).every((condition) => meetsCondition(form, condition));
  }
});
// Toggles in a `data-toggles` row open one panel at a time, as tabs do, and close again when pressed.
document.addEventListener("change", ({ target }) => {
  const row = target.checked && target.closest("[data-toggles]");
  for (const other of row ? row.querySelectorAll("input[type=checkbox]") : []) other.checked = other === target;
});
