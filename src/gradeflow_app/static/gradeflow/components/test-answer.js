// Editing a test answer in place (methods/work.html#answer_editor), new or existing. A successful save closes the
// editor; a failed one keeps what was typed. Closing returns focus to `focusReturn`: the answer, or Add answer.
export const testAnswer = () => ({
  editing: false,
  edit() {
    this.editing = true;
    this.$nextTick(() => {
      // The first field a person can focus, not one a script hides, like the select under Tom Select.
      const fields = this.$refs.form.querySelectorAll("textarea, select, input:not([type=hidden])");
      const field = [...fields].find((control) => control.tabIndex >= 0);
      field.focus();
      // A tick box has no cursor.
      if (field.selectionStart != null) field.setSelectionRange(field.value.length, field.value.length);
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
