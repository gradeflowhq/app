// An assessment's Export page: sending its marks to an assignment, existing or new, in the course's Canvas course,
// after a review that matches students. The send settings are #send-settings, which the server renders as its form
// saves them. Sending decrypts the student IDs in #export-rows-data and matches them to Canvas users.

import { decryptId } from "../../lib/assessment-crypto.js";
import { gradeFor, matchStudents } from "../../lib/canvas-client.js";
import { counted } from "../../lib/words.js";
import { assessmentKeysFor, missingCourseKeyMessage } from "../../services/course-keys.js";
import { readJson } from "../../services/page-data.js";
import { showToast } from "../../services/toasts.js";
import { assignmentGroupsMixin } from "./assignment-groups.js";
import { connectionMixin, mergeMixins, readConfig } from "./connection.js";
import { listPicker, pickerItem } from "./list-picker.js";

const POLL_MS = 2000;
const POLL_LIMIT_MS = 5 * 60_000;
const SENDING = "Sending marks…";

export const canvasExport = () => {
  let students = []; // the Canvas course's, once matched

  return mergeMixins(connectionMixin(), assignmentGroupsMixin(), {
    assignment: listPicker("assignment"),
    newName: "",
    matched: [],
    unmatched: [],
    reviewing: false,
    isMatched: false,

    async init() {
      this.config = readConfig(this.$el);
      this.newName = this.config.name;
      Object.assign(this.assignment, {
        id: this.config.assignment,
        placeholder: () => this.newAssignmentCard,
        onChoose: () => this.changedAssignment(),
      });
      await this.loadConnection();
      if (this.canvasConnection) this.load();
    },
    get courseId() {
      return this.config.course;
    },
    get creating() {
      return this.assignment.id === "new";
    },
    /** The assignment as it's saved: none until a new one is made in Canvas. */
    get savedAssignment() {
      return this.creating ? "" : this.assignment.id;
    },
    /** The card of the assignment to be made, while it's named. */
    get newAssignmentCard() {
      if (!this.creating) return null;
      const group = this.group.card.name;
      const detail = group ? `New, in ${group}` : "New";
      return { ...pickerItem("new", this.newName.trim() || "New assignment", detail), initial: "+" };
    },
    get chosenAssignment() {
      return this.assignment.items.find((assignment) => assignment.id === this.assignment.id);
    },
    get canReview() {
      return Boolean(this.assignment.id && !this.isBusy && (!this.creating || this.newName.trim()));
    },
    get canSend() {
      return this.isMatched && this.matched.length > 0 && !this.isBusy;
    },
    get sending() {
      return this.busyLabel === SENDING;
    },
    get sendLabel() {
      if (this.sending) return "Sending…";
      return this.matched.length
        ? `Send ${counted(this.matched.length, "student's", "students'")} marks`
        : "Send marks";
    },
    get reviewTarget() {
      return `${this.assignment.card.name} · ${this.config.courseName}`;
    },
    /** Says so when marks are scaled to an assignment worth a different number of points. */
    get scaling() {
      const maxMarks = this.matched[0]?.row.maxMarks;
      const pointsPossible = this.chosenAssignment?.pointsPossible;
      if (
        document.getElementById("send-settings").dataset.mode !== "marks" ||
        !pointsPossible ||
        !maxMarks ||
        pointsPossible === maxMarks
      )
        return "";
      return `Totals out of ${maxMarks} are scaled to the assignment's ${counted(pointsPossible, "mark")}.`;
    },

    load() {
      return this.run("Loading assignments…", async () => {
        const assignments = await this.client.assignments(this.courseId);
        await this.loadGroups(this.config.assignmentGroup);
        const groupName = (id) => this.group.items.find((group) => group.id === String(id))?.name;
        this.assignment.items = assignments.map((assignment) => {
          const points = assignment.points_possible != null && counted(assignment.points_possible, "mark");
          const detail = [groupName(assignment.assignment_group_id), points].filter(Boolean).join(" · ");
          return { ...pickerItem(assignment.id, assignment.name, detail), pointsPossible: assignment.points_possible };
        });
        return true;
      });
    },
    newAssignment() {
      this.assignment.id = "new";
      this.assignment.close();
      this.changedAssignment();
    },
    changedAssignment() {
      this.$nextTick(() => this.$refs.sendSettings.dispatchEvent(new Event("change")));
    },

    /** The marks as they are now, matched to the Canvas course's students. */
    async match() {
      const rows = readJson("export-rows-data");
      if (!rows.length) throw new Error("There are no marks to send yet.");
      const assessmentElement = this.$root.closest("[data-assessment]");
      const keys = await assessmentKeysFor(assessmentElement);
      if (!keys) throw new Error(missingCourseKeyMessage(assessmentElement));
      const decryptedRows = await Promise.all(
        rows.map(async (row) => ({ ...row, studentId: await decryptId(row.studentId, keys) })),
      );
      Object.assign(this, matchStudents(decryptedRows, students));
    },
    async review() {
      Object.assign(this, { reviewing: true, isMatched: false, error: "" });
      const dialog = document.getElementById("send-review");
      if (!dialog.open) dialog.showModal();
      await this.run("Matching students…", async () => {
        students = await this.client.students(this.courseId);
        await this.match();
        this.isMatched = true;
      });
    },

    closeReview() {
      Object.assign(this, { reviewing: false, error: "" });
    },
    preventCloseWhileSending(event) {
      if (this.sending) event.preventDefault();
    },

    async send() {
      const { mode, rounding, feedback } = document.getElementById("send-settings").dataset;
      await this.run(SENDING, async () => {
        await this.match(); // marks may have changed since they were checked
        const assignment = await this.targetAssignment();
        // Canvas adds a comment on every send, so nobody gets the same one twice. It lists comments as their text.
        const comments = feedback === "true" ? await this.client.comments(this.courseId, assignment.id) : new Map();
        const text = (html) => new DOMParser().parseFromString(html, "text/html").body.textContent;
        const gradeData = Object.fromEntries(
          this.matched.map(({ row, user }) => [
            user.id,
            {
              posted_grade: gradeFor(row, {
                mode,
                rounding: Number(rounding),
                pointsPossible: assignment.pointsPossible ?? row.maxMarks,
              }),
              ...(feedback === "true" && row.comment && !comments.get(user.id)?.has(text(row.comment))
                ? { text_comment: row.comment }
                : {}),
            },
          ]),
        );
        let progress = await this.client.updateGrades(this.courseId, assignment.id, gradeData);
        const pollUntil = Date.now() + POLL_LIMIT_MS;
        while (progress.workflow_state === "queued" || progress.workflow_state === "running") {
          if (Date.now() > pollUntil)
            throw new Error("Canvas is still updating the grades. Check the assignment in Canvas.");
          await new Promise((resolve) => setTimeout(resolve, POLL_MS));
          progress = await this.client.progress(progress.id);
        }
        if (progress.workflow_state !== "completed")
          throw new Error(progress.message || "Canvas couldn't update the grades.");
        const sent = this.matched.length;
        document.getElementById("send-review").close();
        showToast(`Sent ${counted(sent, "student's", "students'")} marks to “${assignment.name}”.`, "success");
        await htmx.ajax("POST", this.config.sentUrl, {
          source: this.$root,
          target: "#sent-marks",
          swap: "outerHTML",
          values: { assignment_name: assignment.name, student_count: sent },
        });
      });
    },

    async targetAssignment() {
      if (!this.creating) return this.chosenAssignment;
      const maxMarks = this.matched[0]?.row.maxMarks;
      const created = await this.client.createAssignment(this.courseId, {
        name: this.newName.trim(),
        points_possible: maxMarks,
        assignment_group_id: this.group.id,
      });
      const assignment = { ...pickerItem(created.id, created.name), pointsPossible: created.points_possible };
      this.assignment.items.push(assignment);
      this.assignment.id = assignment.id;
      this.changedAssignment();
      return assignment;
    },
  });
};
