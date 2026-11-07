// The landing page's demos: accepting an answer group, the marking examples and sending to Canvas. The page holds
// every mark they show, as the core gave them; these only choose which to show.

import { showToast } from "../services/toasts.js";

const SENDING_MS = 1400;

// One of the grouping demo's groups: accepted once all its answers are.
export const answerGroup = () => ({
  answers: [],
  init() {
    this.answers = JSON.parse(this.$el.dataset.answers);
  },
  get isAccepted() {
    return this.answers.every((answer) => this.accepted.includes(answer));
  },
  fullMarks() {
    const added = this.answers.filter((answer) => !this.accepted.includes(answer));
    this.accepted.push(...added);
    showToast("Full marks for this answer group.", "success");
  },
});

// The example shown, and its variant: the example's name and what the form holds for its setting.
export const markingExamples = () => ({
  example: "",
  variant: "",
  init() {
    this.change();
  },
  change() {
    const data = new FormData(this.$el);
    this.example = data.get("example");
    this.variant = `${this.example}:${data.getAll(this.example).join(",")}`;
  },
});

export const canvasSend = () => ({
  sending: false,
  async send() {
    this.sending = true;
    await new Promise((resolve) => setTimeout(resolve, SENDING_MS));
    this.sending = false;
    showToast("Sent 808 students' marks to “Final Exam”.", "success");
  },
});
