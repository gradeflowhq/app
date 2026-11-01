// Lists searched as you type or chosen from, and the method picker, which also chooses a method to read its guide and use it.

import { matches } from "./words.js";

// Shows the `[data-search]` items that have every word typed, the sections that still have one, and `[data-empty]`
// when nothing typed matches. With nothing typed, it shows the items `listed`: all of them.
const searchList = () => ({
  query: "",

  search(event) {
    this.query = event.target.value;
    this.filter();
  },

  listed() {
    return true;
  },

  filter() {
    const typed = Boolean(this.query.trim());
    for (const item of this.$root.querySelectorAll("[data-search]")) {
      item.hidden = typed ? !matches(item.dataset.search, this.query) : !this.listed(item);
    }
    for (const section of this.$root.querySelectorAll("[data-section]")) {
      section.hidden = !section.querySelector("[data-search]:not([hidden])");
    }
    this.$root.querySelector("[data-empty]").hidden =
      !typed || Boolean(this.$root.querySelector("[data-search]:not([hidden])"));
  },
});

// A searchable list of options, chosen once one of its radios is.
export const choiceList = () => ({
  ...searchList(),
  chosen: false,

  init() {
    this.chosen = Boolean(this.$root.querySelector("[type=radio]:checked"));
  },

  choose(event) {
    if (event.target.type === "radio") this.chosen = true;
  },
});

// Its Built-in, Yours and Public tabs each list their own methods; search finds methods in all three.
export const methodPicker = () => ({
  ...searchList(),
  tab: "",
  selected: "",
  selectedLabel: "",

  init() {
    this.tab = this.$root.dataset.tab;
    this.selected = this.$root.dataset.selected;
    const chosen = this.$root.querySelector(`[data-method="${this.selected}"], [aria-current="true"]`);
    this.selectedLabel = chosen?.dataset.label ?? "";
    chosen?.scrollIntoView({ block: "nearest" });
  },

  shows(tab) {
    return !this.query && this.tab === tab;
  },

  listed(item) {
    return item.closest("[data-tab]").dataset.tab === this.tab;
  },

  show(event) {
    this.tab = event.currentTarget.dataset.tab;
    this.query = this.$refs.search.value = "";
    this.filter();
    this.$refs.list.scrollTop = 0;
  },

  // Arrow keys move to the other tab, as tabs do.
  next(event) {
    const tabs = [...this.$root.querySelectorAll("[role=tab]")];
    const next = tabs[(tabs.indexOf(event.target) + 1) % tabs.length];
    next.focus();
    next.click();
  },

  get unchanged() {
    return this.selected === this.$root.dataset.current;
  },

  choose(event) {
    const chosen = event.target.closest("[data-method]");
    if (!chosen) return;
    this.selected = chosen.dataset.method;
    this.selectedLabel = chosen.dataset.label;
    for (const each of this.$root.querySelectorAll("[data-method]")) each.setAttribute("aria-current", String(each === chosen));
  },
});
