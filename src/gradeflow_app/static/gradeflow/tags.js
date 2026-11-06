// Tom Select over a `<select multiple>`: removable values dragged into order, the most common answers as
// suggestions (their count in `data-data`), and any new value typed in.

import { loadScript } from "./vendor.js";

export const tagInput = () => ({
  select: null,
  async init() {
    await loadScript("tom-select-2.6.2/tom-select.complete.min.js");
    // Morphs are synchronous: Tom Select is built again from the select once the morph has brought it up to date.
    this.$el.addEventListener("morph", () => {
      this.destroy();
      queueMicrotask(() => this.build());
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
      persist: false,
      maxOptions: 100,
      plugins: ["remove_button", "drag_drop"],
      wrapperClass: "ts-wrapper input w-full",
      sortField: [{ field: "count", direction: "desc" }, { field: "$order" }],
      render: {
        option: (data, escape) =>
          `<div><span>${escape(data.text)}</span>${data.count ? `<span class="tabular-nums text-xs text-muted">${data.count}</span>` : ""}</div>`,
        option_create: (data, escape) => `<div class="create">Add <strong>${escape(data.input)}</strong></div>`,
        no_results: () => null,
      },
      onItemAdd() {
        this.setTextboxValue("");
        this.close();
      },
    });
    // A click in the box keeps it focused for the next value: Tom Select's own click on an open box blurs it.
    const originalClick = this.select.onClick.bind(this.select);
    this.select.onClick = () => {
      if (!(this.select.isFocused && this.select.isOpen)) originalClick();
    };
    // Esc closes the list of suggestions, not the dialog the field is in, with what was typed in it.
    this.select.wrapper.addEventListener("keydown", (event) => {
      if (event.key === "Escape") event.preventDefault();
    });
  },
  destroy() {
    this.select?.destroy();
    this.select = null;
  },
});
