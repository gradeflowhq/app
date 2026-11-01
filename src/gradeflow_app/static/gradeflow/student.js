// A question in the student drawer: its Adjust form, shown on demand, and where the feedback comes from.

export const adjustment = () => ({
  open: false,
  feedback: "",
  init() {
    this.feedback = this.$root.dataset.feedback;
  },
  get writing() {
    return this.feedback === "own";
  },
  get clearing() {
    return this.feedback === "none";
  },
  show() {
    this.open = true;
    this.$nextTick(() => this.$root.querySelector("input[name=marks]").focus());
  },
  cancel() {
    this.open = false;
    this.$root.querySelector("form").reset();
    this.init();
  },
});
