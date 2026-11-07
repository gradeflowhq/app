// Markdown fields (forms/markdown.html): formatting buttons with their usual shortcuts, lists that continue on Enter,
// and a preview the server renders as its pages show the text.

import { vendorUrl } from "../services/vendor.js";

const SHORTCUTS = { b: "md-bold", i: "md-italic", k: "md-link" };
const LIST_ITEM = /^(\s*)(?:([-*])|(\d+)\.)(\s+)(.*)$/;

// Edits go through the browser's own typing, as the toolbar's do, so Undo takes them back.
const type = (text) => document.execCommand("insertText", false, text);

export const markdownEditor = () => ({
  isWriting: true,
  init() {
    import(vendorUrl("markdown-toolbar-element-2.2.3/index.js"));
  },
  write() {
    this.isWriting = true;
    this.$nextTick(() => this.$refs.text.focus());
  },
  preview() {
    this.isWriting = false;
    htmx.ajax("POST", this.$root.dataset.preview, {
      target: this.$refs.preview,
      swap: "innerHTML",
      values: { text: this.$refs.text.value },
    });
  },
  keydown(event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) this.continueList(event);
    const button =
      (event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey && SHORTCUTS[event.key.toLowerCase()];
    if (!button) return;
    event.preventDefault();
    this.$root.querySelector(button).click();
  },
  // Enter in a list item starts the next one; in an empty one, it ends the list with a blank line, which Markdown
  // needs before the next paragraph.
  continueList(event) {
    const field = event.target;
    const { selectionStart: cursor, selectionEnd, value } = field;
    const start = value.lastIndexOf("\n", cursor - 1) + 1;
    const item = cursor === selectionEnd && value.slice(start, cursor).match(LIST_ITEM);
    if (!item) return;
    event.preventDefault();
    const [, indent, bullet, number, space, text] = item;
    if (!text) field.setSelectionRange(start, cursor);
    type(text ? `\n${indent}${bullet ?? `${Number(number) + 1}.`}${space}` : "\n");
  },
});
