// Form fields that do more than the browser does.

const currentValue = (control) => (control?.type === "checkbox" ? control.checked : control?.value);
const isValue = (value, wanted) =>
  typeof wanted === "boolean" ? Boolean(value) === wanted : String(value) === String(wanted);
const meetsCondition = (form, [name, wanted]) =>
  wanted.some((option) => isValue(currentValue(form.elements.namedItem(name)), option));

let lastFeedbackField;

export function start() {
  // A `data-insert` button types its value at the cursor of the feedback field used last.
  document.addEventListener("focusin", (event) => {
    if (event.target.matches("[data-feedback-fields] textarea")) lastFeedbackField = event.target;
  });
  document.addEventListener("click", (event) => {
    const insert = event.target.closest("[data-insert]");
    if (!insert) return;
    const fields = insert.closest("[data-feedback-fields]");
    const field = fields.contains(lastFeedbackField) ? lastFeedbackField : fields.querySelector("textarea");
    if (!field) return;
    const cursor = field.selectionStart ?? field.value.length;
    field.setRangeText(insert.dataset.insert, cursor, field.selectionEnd ?? cursor, "end");
    field.focus();
    field.dispatchEvent(new Event("change", { bubbles: true }));
  });
  // A `data-preset` button sets the field its group names, as typing would.
  document.addEventListener("click", (event) => {
    const preset = event.target.closest("[data-preset]");
    const input = preset && document.getElementById(preset.closest("[data-presets]").dataset.presets);
    if (!input) return;
    input.value = preset.dataset.preset;
    for (const other of preset.parentElement.children) other.setAttribute("aria-pressed", String(other === preset));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  // A morph keeps what was typed, so a `data-resets` button empties its form once its request succeeds.
  document.addEventListener("htmx:finally:request", ({ detail: { ctx } }) => {
    if (ctx.response?.status < 400 && ctx.request.submitter?.hasAttribute("data-resets")) ctx.request.form?.reset();
  });
  // A `data-visible-when` field shows while each field it names has one of the values listed for it
  // (schemaforms.HintedField).
  document.addEventListener("change", ({ target: { form } }) => {
    for (const field of form?.querySelectorAll("[data-visible-when]") ?? []) {
      field.hidden = !Object.entries(JSON.parse(field.dataset.visibleWhen)).every((condition) =>
        meetsCondition(form, condition),
      );
    }
  });
  // The toggles in a `data-toggles` row open one panel at a time, as tabs do, and close when pressed again.
  document.addEventListener("change", ({ target }) => {
    const row = target.checked && target.closest("[data-toggles]");
    for (const other of row ? row.querySelectorAll("input[type=checkbox]") : []) other.checked = other === target;
  });
}
