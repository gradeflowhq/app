// An assessment's Export page: sending its marks to an assignment, existing or new, in the course's Canvas course,
// after a review that matches students. The send settings are #send-settings, which the server renders as its form
// saves them. Sending decrypts the student IDs in #export-rows-data and matches them to Canvas users.

import { decryptId } from "../../lib/assessment-crypto.js";
import { displayedGrade, gradeFor, matchStudents } from "../../lib/canvas-client.js";
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
    conflicts: [],
    unmatchedUsers: [],
    reviewing: false,
    isMatched: false,
    preview: {
      rows: [],
      index: 0,
      studentId: "",
      grade: "",
      gradeFlowMarks: "",
      needsMarking: "",
      commentPlaceholder: "",
    },

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
    /** The course's page in Canvas, once connected. */
    get courseUrl() {
      return this.canvasConnection ? `${this.canvasConnection.baseUrl}/courses/${this.courseId}` : null;
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
    get isLoadingAssignments() {
      return this.isBusy && !this.assignment.items.length;
    },
    get canReview() {
      return Boolean(this.connected && this.assignment.id && !this.isBusy && (!this.creating || this.newName.trim()));
    },
    get canSend() {
      return this.isMatched && this.matched.length > 0 && !this.isBusy;
    },
    get sending() {
      return this.busyLabel === SENDING;
    },
    get sendLabel() {
      if (this.sending) return "Sending…";
      if (this.unmarkedCount) return "Send anyway";
      return this.matched.length
        ? `Send ${counted(this.matched.length, "student's", "students'")} marks`
        : "Send marks";
    },
    /** How many of the matched students' answers need marking. */
    get unmarkedCount() {
      return this.matched.reduce((count, { row }) => count + row.unmarkedCount, 0);
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
          return {
            ...pickerItem(assignment.id, assignment.name, detail),
            pointsPossible: assignment.points_possible,
            gradingType: assignment.grading_type,
          };
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

    /** `row`'s `posted_grade` with the send settings, on an assignment worth `pointsPossible`. A new assignment is
     * worth the row's max marks. */
    postedGrade(row, pointsPossible) {
      const { mode, rounding } = document.getElementById("send-settings").dataset;
      return gradeFor(row, { mode, rounding: Number(rounding), pointsPossible: pointsPossible ?? row.maxMarks });
    },

    /** The grade Canvas shows for `row` on the chosen assignment, or on a new one. */
    canvasGrade(row) {
      const { gradingType = "points", pointsPossible } = this.chosenAssignment ?? {};
      const grade = this.postedGrade(row, pointsPossible);
      return grade === null
        ? "—"
        : displayedGrade(grade, { gradingType, pointsPossible: pointsPossible ?? row.maxMarks });
    },

    /** The assignment's name as students see it: the chosen one's, or the assessment's for a new one. */
    get previewAssignmentName() {
      return this.assignment.card.name || this.config.name;
    },
    get previewPosition() {
      return `${this.preview.index + 1} of ${this.preview.rows.length}`;
    },
    openPreview() {
      this.preview.rows = readJson("export-rows-data");
      this.showPreview(0);
    },
    /** Shows the `index`th row's grade and comment as Canvas would; an index past either end does nothing. */
    async showPreview(index) {
      const row = this.preview.rows[index];
      if (!row) return;
      const grade = this.canvasGrade(row);
      const gradeFlowMarks = displayedGrade(row.marks, { gradingType: "points", pointsPossible: row.maxMarks });
      const sendsFeedback = document.getElementById("send-settings").dataset.feedback === "true";
      this.$refs.previewComment.innerHTML = sendsFeedback ? row.comment : "";
      this.$refs.previewComment.scrollTop = 0;
      Object.assign(this.preview, {
        index,
        studentId: "",
        grade,
        gradeFlowMarks: grade === gradeFlowMarks ? "" : gradeFlowMarks,
        needsMarking: row.unmarkedCount ? `${counted(row.unmarkedCount, "answer needs", "answers need")} marking` : "",
        commentPlaceholder: !sendsFeedback ? "Feedback isn't sent." : row.comment ? "" : "Nothing marked yet.",
      });
      const keys = await assessmentKeysFor(this.$root.closest("[data-assessment]")).catch(() => null);
      const studentId = keys && (await decryptId(row.studentId, keys).catch(() => null));
      if (this.preview.index === index) this.preview.studentId = studentId || "Encrypted";
    },

    async send() {
      const { feedback } = document.getElementById("send-settings").dataset;
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
              posted_grade: this.postedGrade(row, assignment.pointsPossible),
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
      const assignment = {
        ...pickerItem(created.id, created.name),
        pointsPossible: created.points_possible,
        gradingType: created.grading_type,
      };
      this.assignment.items.push(assignment);
      this.assignment.id = assignment.id;
      this.changedAssignment();
      return assignment;
    },
  });
};
