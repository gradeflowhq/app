// Form fields that do more than the browser does.

const currentValue = (control) => (control?.type === "checkbox" ? control.checked : control?.value);
const isValue = (value, wanted) =>
  typeof wanted === "boolean" ? Boolean(value) === wanted : String(value) === String(wanted);
const meetsCondition = (form, [name, wanted]) =>
  wanted.some((option) => isValue(currentValue(form.elements.namedItem(name)), option));

const textBoxes = "textarea, input[type=text]";
let lastTextBox;

export function start() {
  // A `data-insert` button inserts its value at the cursor of its field's text box.
  document.addEventListener("focusin", (event) => {
    if (event.target.matches(`[data-inserts] :is(${textBoxes})`)) lastTextBox = event.target;
  });
  // Keeps the text box focused and a tag input open.
  document.addEventListener(
    "mousedown",
    (event) => {
      if (!event.target.closest("[data-insert]")) return;
      event.preventDefault();
      event.stopPropagation();
    },
    { capture: true },
  );
  document.addEventListener("click", (event) => {
    const insert = event.target.closest("[data-insert]");
    if (!insert) return;
    const field = insert.closest("[data-inserts]");
    const box =
      [document.activeElement, lastTextBox].find((each) => field.contains(each) && each.matches(textBoxes)) ??
      field.querySelector(textBoxes);
    if (!box) return;
    const cursor = box.selectionStart ?? box.value.length;
    box.setRangeText(insert.dataset.insert, cursor, box.selectionEnd ?? cursor, "end");
    box.focus();
    // A tag input adds the text as a tag; other boxes save as typing does, as saving at once could lose later typing.
    const tomSelect = field.querySelector("select")?.tomselect;
    if (tomSelect) tomSelect.createItem(box.value);
    else box.dispatchEvent(new Event("input", { bubbles: true }));
  });
  // A select that sent its change shows what the server kept: a morph leaves it as picked, even when refused.
  document.addEventListener("htmx:after:swap", ({ detail: { ctx } }) => {
    const select = ctx.sourceEvent?.target;
    if (select?.type !== "select-one" || !select.isConnected) return;
    for (const option of select.options) option.selected = option.defaultSelected;
  });
  // Enter in an autosaving form (hx-trigger without submit) would submit it as a GET, with the CSRF token in the URL.
  document.addEventListener("submit", (event) => {
    const hxTrigger = event.target.getAttribute("hx-trigger");
    if (hxTrigger && !/\bsubmit\b/.test(hxTrigger)) event.preventDefault();
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
  // A form shown by a toggle closes when reset: the toggle that `aria-controls` it is unchecked, and takes focus.
  document.addEventListener("reset", ({ target }) => {
    const toggle = document.querySelector(`[aria-controls="${target.id}"]:checked`);
    if (!toggle) return;
    toggle.checked = false;
    toggle.focus();
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
