// The question list shown over the work below 1,024 px. Esc closes it, giving focus back to its toggle.

export const questionsToggle = () => ({
  close() {
    if (!this.$el.checked) return;
    this.$el.checked = false;
    this.$el.focus();
  },
});
