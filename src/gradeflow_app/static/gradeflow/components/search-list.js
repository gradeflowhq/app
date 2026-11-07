// Lists filtered as you type: a plain list, a list of radio choices, and the method picker.

import { matches } from "../lib/words.js";

// Shows the `[data-search]` items that have every word typed, the sections that still have one, and `[data-empty]`
// when nothing typed matches. With nothing typed, it shows the items `isListed`: all of them.
export const searchList = () => ({
  query: "",

  search(event) {
    this.query = event.target.value;
    this.filter();
  },

  isListed() {
    return true;
  },

  filter() {
    const typed = Boolean(this.query.trim());
    for (const item of this.$root.querySelectorAll("[data-search]")) {
      item.hidden = typed ? !matches(item.dataset.search, this.query) : !this.isListed(item);
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
  hasChoice: false,

  init() {
    this.hasChoice = Boolean(this.$root.querySelector("[type=radio]:checked"));
  },

  choose(event) {
    if (event.target.type === "radio") this.hasChoice = true;
  },
});

// The method picker: its Built-in, Yours and Public tabs each list their own methods, and a search covers all three.
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

  isTabShown(tab) {
    return !this.query && this.tab === tab;
  },

  isListed(item) {
    return item.closest("[data-tab]").dataset.tab === this.tab;
  },

  showTab(event) {
    this.tab = event.currentTarget.dataset.tab;
    this.query = this.$refs.search.value = "";
    this.filter();
    this.$refs.list.scrollTop = 0;
  },

  // Arrow keys move to the other tab, as tabs do.
  focusNextTab(event) {
    const tabs = [...this.$root.querySelectorAll("[role=tab]")];
    const next = tabs[(tabs.indexOf(event.target) + 1) % tabs.length];
    next.focus();
    next.click();
  },

  get isCurrentMethod() {
    return this.selected === this.$root.dataset.currentMethod;
  },

  choose(event) {
    const chosen = event.target.closest("[data-method]");
    if (!chosen) return;
    this.selected = chosen.dataset.method;
    this.selectedLabel = chosen.dataset.label;
    for (const method of this.$root.querySelectorAll("[data-method]"))
      method.setAttribute("aria-current", String(method === chosen));
  },
});
