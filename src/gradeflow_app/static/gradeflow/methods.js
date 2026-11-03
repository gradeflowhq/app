// The method page (methods/work.html).

// htmx reads a request's fields when it's triggered, so an edit queued behind another (`hx-sync: queue`) would carry
// the revision of the page before the first one answered, and be refused as if another tab sent it. It sends the
// revision the page shows when it goes.
document.addEventListener("htmx:before:request", (event) => {
  const { body } = event.detail.ctx.request;
  const revisionField = document.querySelector("#method-state [name=revision]");
  if (revisionField && body?.has?.("revision")) body.set("revision", revisionField.value);
});

// A test answer written in place (methods/work.html#answer_editor): a new one, or a row's in place of its text. The
// box closes once its save worked, back to the saved text; a save that didn't keeps what was written. Closed, focus
// goes back to where the edit started (`focusReturn`): the answer, or Add answer.
export const testAnswer = () => ({
  editing: false,
  edit() {
    this.editing = true;
    this.$nextTick(() => {
      // Not a control a script stands in for, like the select under Tom Select's values.
      const fields = this.$refs.form.querySelectorAll("textarea, select, input:not([type=hidden])");
      const field = [...fields].find((control) => control.tabIndex >= 0);
      field.focus();
      if (field.selectionStart != null) field.setSelectionRange(field.value.length, field.value.length); // not a tick box
    });
  },
  // Enter saves the answer as it is written; Shift+Enter starts a new line.
  submit(event) {
    if (event.shiftKey) return;
    event.preventDefault();
    this.$refs.form.requestSubmit();
  },
  // Esc that a control used, such as to close the suggestions, leaves the edit open.
  escape(event) {
    if (!event.defaultPrevented) this.cancel();
  },
  cancel() {
    this.$refs.form.reset();
    this.editing = false;
    this.$nextTick(() => this.$refs.focusReturn?.focus());
  },
  // Other requests' events reach the form too, such as a test run's.
  afterSave(event) {
    const { sourceElement, response } = event.detail.ctx;
    if (sourceElement === this.$refs.form && response?.status < 400) this.cancel();
  },
});
