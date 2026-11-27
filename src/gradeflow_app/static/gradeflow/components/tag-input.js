// Tom Select over a `<select multiple>`: removable values dragged into order, the most common answers as
// suggestions (their count in `data-data`), and any new value typed in.

import { loadScript } from "../services/vendor.js";

export const tagInput = () => ({
  select: null,
  async init() {
    await loadScript("tom-select-2.6.2/tom-select.complete.min.js");
    // Only a tag added or removed autosaves the form: typing, Tom Select's own input events and the text box's change
    // events stay inside.
    this.$el.addEventListener("input", (event) => event.stopPropagation());
    // Morphs are synchronous: Tom Select is built again from the select once the morph has brought it up to date. A
    // focused tag input keeps its state, as htmx keeps a focused text input's value: an item added while an earlier
    // save was in flight would otherwise be lost.
    this.$el.addEventListener("morph", () => {
      const controlInput = this.select?.control_input;
      const focusedState = controlInput === document.activeElement && {
        items: this.select.items,
        inputValue: controlInput.value,
        selectionStart: controlInput.selectionStart,
        selectionEnd: controlInput.selectionEnd,
        isOpen: this.select.isOpen,
      };
      // Chromium blurs a focused element when it's removed, and Tom Select adds the text typed in it as an item on blur.
      if (focusedState) controlInput.value = "";
      this.destroy();
      queueMicrotask(() => {
        this.build();
        if (!focusedState || !this.select) return;
        const { items, inputValue, selectionStart, selectionEnd, isOpen } = focusedState;
        this.select.addOptions(items.map((value) => ({ value, text: value })));
        // Silent, as their own change already saved them. Set before the text, which onItemAdd clears.
        this.select.setValue(items, true);
        this.select.setTextboxValue(inputValue);
        // Focusing opens the dropdown.
        this.select.control_input.focus();
        this.select.control_input.setSelectionRange(selectionStart, selectionEnd);
        if (!isOpen) this.select.close(false);
      });
    });
    this.build();
  },
  build() {
    const element = this.$el;
    if (!element.isConnected) return;
    this.select = new TomSelect(element, {
      create: element.dataset.create !== "false",
      addPrecedence: true,
      // Commas belong to values, as in an accepted answer "LRR, RR": typing or pasting one doesn't split it.
      delimiter: null,
      dataAttr: "data",
      createOnBlur: true,
      // Enter adds what was typed, however fast; the list closes after each value added, picked or typed, so it
      // doesn't cover what's below.
      refreshThrottle: 0,
      closeAfterSelect: true,
      maxOptions: 100,
      plugins: ["remove_button", "drag_drop"],
      wrapperClass: "ts-wrapper input w-full",
      // A textarea, so a long value wraps as it's typed.
      controlInput: '<textarea rows="1" autocomplete="off"></textarea>',
      sortField: [{ field: "count", direction: "desc" }, { field: "$order" }],
      render: {
        option: (data, escapeHtml) =>
          `<div><span>${escapeHtml(data.text)}</span>${data.count ? `<span class="tabular-nums text-xs text-muted">${data.count}</span>` : ""}</div>`,
        option_create: (data, escapeHtml) => `<div class="create">Add <strong>${escapeHtml(data.input)}</strong></div>`,
        no_results: () => null,
      },
      onItemAdd() {
        this.setTextboxValue("");
        this.close();
      },
    });
    this.select.wrapper.addEventListener("input", (event) => event.stopPropagation());
    this.select.wrapper.addEventListener("change", (event) => event.stopPropagation());
    // A click in the box keeps it focused for the next value: Tom Select's own click on an open box blurs it.
    const originalClick = this.select.onClick.bind(this.select);
    this.select.onClick = () => {
      if (!(this.select.isFocused && this.select.isOpen)) originalClick();
    };
    // Tom Select listens for clicks on its control, which the box's padding surrounds.
    this.select.wrapper.addEventListener("click", (event) => {
      if (event.target === this.select.wrapper) this.select.onClick();
    });
    // A click on a value opens the suggestions, as a click elsewhere in the box does.
    this.select.onItemSelect = () => false;
    // A drag that leaves the order unchanged was a click that moved.
    let order;
    this.select.wrapper.addEventListener("dragstart", () => {
      order = this.select.items.join("\n");
    });
    this.select.wrapper.addEventListener("dragend", () => {
      if (this.select.items.join("\n") === order) this.select.focus();
    });
    // Esc closes the list of suggestions, not the dialog the field is in, with what was typed in it. Enter adds what was
    // typed rather than a line break; Shift+Enter adds a line break, kept from Tom Select, which treats it as Enter.
    this.select.wrapper.addEventListener(
      "keydown",
      (event) => {
        if (event.key === "Enter" && event.shiftKey) event.stopPropagation();
        else if (event.key === "Escape" || event.key === "Enter") event.preventDefault();
      },
      { capture: true },
    );
  },
  destroy() {
    this.select?.destroy();
    this.select = null;
  },
});
