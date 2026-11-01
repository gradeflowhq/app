// The landing page's demos: marking answers, the marking examples, sending to Canvas and showing student IDs. The
// page holds every mark they show, as the core gave them; these only choose which to show.

import { report } from "./toasts.js";

const SENDING_MS = 1400;

export const answerGroups = () => ({
  grouping: "meaning",
  accepted: [],
  init() {
    this.accepted = JSON.parse(this.$el.dataset.accepted);
  },
});

// One of answerGroups' groups: accepted once all its answers are.
export const answerGroup = () => ({
  answers: [],
  init() {
    this.answers = JSON.parse(this.$el.dataset.answers);
  },
  get done() {
    return this.answers.every((answer) => this.accepted.includes(answer));
  },
  fullMarks() {
    const added = this.answers.filter((answer) => !this.accepted.includes(answer));
    this.accepted.push(...added);
    report("Full marks for this answer group.", "success");
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
    report("Sent 808 students' marks to “Final Exam”.", "success");
  },
});

export const studentIds = () => ({
  shown: false,
  toggle() {
    this.shown = !this.shown;
  },
  get status() {
    return this.shown ? "Decrypted in this browser" : "Student IDs are encrypted";
  },
  get action() {
    return this.shown ? "Forget" : "Show IDs";
  },
  get actionStyle() {
    return this.shown ? "" : "btn-primary";
  },
});
