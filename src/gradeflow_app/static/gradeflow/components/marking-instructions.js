// The marking panel's band of marking instructions: shown with them, and opened on its field by its Edit, the Draft
// with AI row or the editor's Ask AI. Each is a component of its own, as htmx morphs them apart.

const TOGGLE_EVENT = "marking-instructions-toggle";
const SHOWN_EVENT = "marking-instructions-shown";

export const markingInstructions = () => ({
  editing: false,
  shown: false,
  init() {
    this.read();
    // Server refreshes set data-editing and data-shown, as when AI finishes.
    new MutationObserver(() => this.read()).observe(this.$root, { attributeFilter: ["data-editing", "data-shown"] });
    this.$watch("isHidden", (hidden) => this.$dispatch(SHOWN_EVENT, { shown: !hidden }));
  },
  get isHidden() {
    return !this.editing && !this.shown;
  },
  read() {
    this.editing = this.$root.dataset.editing === "true";
    this.shown = this.$root.dataset.shown === "true";
  },
  edit() {
    this.editing = true;
    // A morphed-in field has no x-ref, so it's found by its tag.
    this.$nextTick(() => this.$root.querySelector("textarea").focus());
  },
  stopEditing() {
    this.editing = false;
  },
  toggleEditing() {
    if (this.editing) this.stopEditing();
    else this.edit();
  },
});

/** A button outside the band that opens its field, pressed while the band shows: the Draft with AI row or Ask AI. */
export const markingInstructionsToggle = () => ({
  pressed: false,
  init() {
    this.pressed = this.$el.dataset.pressed === "true";
  },
  toggle() {
    this.$dispatch(TOGGLE_EVENT);
  },
  sync(event) {
    this.pressed = event.detail.shown;
  },
});
