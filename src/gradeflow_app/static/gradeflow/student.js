// A question in the student drawer: its Adjust form, shown on demand, and where the feedback comes from.

export const adjustForm = () => ({
  isFormOpen: false,
  feedback: "",
  init() {
    this.feedback = this.$root.dataset.feedback;
  },
  show() {
    this.isFormOpen = true;
    this.$nextTick(() => this.$root.querySelector("input[name=marks]").focus());
  },
  cancel() {
    this.isFormOpen = false;
    this.$root.querySelector("form").reset();
    this.init();
  },
});
