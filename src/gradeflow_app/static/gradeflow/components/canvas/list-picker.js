// A Canvas list, such as courses or assignments, chosen from in a dialog (exports/picker.html).

import { matches } from "../../lib/words.js";

const NO_CARD = Object.freeze({ id: "", name: "", detail: "", initial: "" });

/** One of a Canvas list's items as a picker shows it. */
export const pickerItem = (id, name, detail = "") => ({
  id: String(id),
  name,
  detail,
  initial: name.charAt(0),
  search: `${name} ${detail}`.toLowerCase(),
});

/**
 * Choosing `id` from `items` in the dialog `#<key>-picker`. `card` is the chosen item, else `placeholder()`'s, such
 * as the saved choice while the list loads, else an empty card whose fields a template can read without a check.
 * `onChoose` runs after a choice; `naming` is true while the dialog names a new item.
 */
export const listPicker = (key) => ({
  id: "",
  items: [],
  query: "",
  naming: false,
  placeholder: () => null,
  onChoose: () => {},

  get card() {
    return this.items.find((item) => item.id === this.id) ?? this.placeholder() ?? NO_CARD;
  },
  get shown() {
    return this.items
      .filter((item) => matches(item.search, this.query))
      .map((item) => ({ ...item, selected: item.id === this.id }));
  },
  get dialog() {
    return document.getElementById(`${key}-picker`);
  },
  open() {
    Object.assign(this, { query: "", naming: false });
    this.dialog.showModal();
    this.dialog.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  },
  choose(event) {
    this.id = event.target.closest("[data-id]").dataset.id;
    this.close();
    this.onChoose();
  },
  close() {
    this.dialog.close();
  },
});
