// An assessment's Export page: its students matched to the Canvas course's, with the grade Canvas has for each and
// the grade sending gives, and sending the grades that change to the chosen assignment. #send-settings holds
// the send settings and #export-rows-data the marks; the server re-renders both, and each runs its `x-init` when
// swapped in.

import { decryptId } from "../../lib/assessment-crypto.js";
import { displayedGrade, gradebookGrade, gradeFor, matchStudents } from "../../lib/canvas-client.js";
import { counted, matches, naturalOrder } from "../../lib/words.js";
import { assessmentKeysFor, missingCourseKeyMessage } from "../../services/course-keys.js";
import { openDialog } from "../../services/dialogs.js";
import { showToast } from "../../services/toasts.js";
import { assignmentPickerMixin, marksLabel } from "./assignment-picker.js";
import { connectionMixin, mergeMixins, readConfig } from "./connection.js";

const POLL_MS = 2000;
const POLL_LIMIT_MS = 5 * 60_000;
const SENDING = "Sending marks…";

const STATUS_ORDER = { not_in_canvas: 0, duplicate_id: 1, changed: 2, unchanged: 3 };
const FILTERS = [
  { key: "all", label: "All", test: () => true },
  { key: "changed", label: "To send", test: (student) => student.status === "changed" },
  { key: "needs_marking", label: "Needs marking", test: (student) => student.row.unmarkedCount > 0 },
  { key: "not_in_canvas", label: "Not in Canvas", test: (student) => student.status === "not_in_canvas" },
  { key: "duplicate_id", label: "Duplicate ID", test: (student) => student.status === "duplicate_id" },
];

/** The marks to send, as the server last rendered them in #export-rows-data. */
const readRows = () => JSON.parse(document.getElementById("export-rows-data").textContent);

/** A comment's text, as Canvas lists the comments it has. */
const commentText = (html) => new DOMParser().parseFromString(html, "text/html").body.textContent;

export const canvasExport = () => {
  let canvasUsers = []; // the Canvas course's students

  return mergeMixins(connectionMixin(), assignmentPickerMixin(), {
    settings: {},
    maxMarks: 0,
    rowCount: 0,
    matched: [],
    unmatched: [],
    conflicts: [],
    unmatchedUsers: [],
    isMatched: false,
    submissions: null, // the chosen assignment's, by user ID; null until they load
    filter: "all",
    query: "",
    reviewing: false,
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
      this.assignmentId = this.config.assignment;
      this.maxMarks = readRows()[0]?.maxMarks ?? 0;
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
    /** The chosen assignment's group and marks, and whether totals are scaled to its points. */
    get assignmentDetail() {
      const pointsPossible = this.chosenAssignment?.pointsPossible;
      const scaled =
        this.settings.mode === "marks" && pointsPossible && this.maxMarks && pointsPossible !== this.maxMarks;
      return [this.chosenGroup?.name, marksLabel(pointsPossible), scaled && `scaled from ${this.maxMarks}`]
        .filter(Boolean)
        .join(" · ");
    },
    get isLoadingAssignments() {
      return this.isBusy && !this.groups.length;
    },

    readSettings() {
      this.settings = { ...document.getElementById("send-settings").dataset };
    },

    /** Loads the course's students and assignments, choosing the one named after the assessment when none is saved,
     * and the chosen assignment's submissions. */
    load() {
      return this.run("Loading the Canvas course…", async () => {
        [canvasUsers] = await Promise.all([
          this.client.students(this.courseId),
          this.loadGroups().then(() => {
            if (!this.chosenAssignment)
              this.assignmentId = this.assignments.find(({ name }) => name === this.config.name)?.id ?? "";
            return this.loadSubmissions();
          }),
        ]);
        await this.match();
        return true;
      });
    },
    /** The chosen assignment's submissions; none without one. The previous assignment's stay until they load. */
    async loadSubmissions() {
      const id = this.assignmentId;
      const submissions = id ? await this.client.submissions(this.courseId, id) : new Map();
      if (this.assignmentId === id) this.submissions = submissions;
    },
    choseAssignment() {
      this.saveAssignment();
      this.run("Loading grades…", () => this.loadSubmissions());
    },
    saveAssignment() {
      this.$nextTick(() => document.getElementById("send-settings-form").dispatchEvent(new Event("change")));
    },

    /** Matches the marks as they are now to the Canvas course's students. */
    async match() {
      const rows = readRows();
      const assessmentElement = this.$root.closest("[data-assessment]");
      const keys = await assessmentKeysFor(assessmentElement);
      if (!keys) throw new Error(missingCourseKeyMessage(assessmentElement));
      const decryptedRows = await Promise.all(
        rows.map(async (row) => ({
          ...row,
          studentId: await decryptId(row.studentId, keys),
          commentText: commentText(row.comment),
        })),
      );
      Object.assign(this, matchStudents(decryptedRows, canvasUsers), {
        maxMarks: rows[0]?.maxMarks ?? 0,
        rowCount: rows.length,
        isMatched: true,
      });
    },
    /** Once the students are matched and the chosen assignment's submissions have loaded. */
    get isStudentListReady() {
      return this.isMatched && Boolean(this.submissions);
    },
    /** Matches the marks again once they've refreshed. */
    rematch() {
      if (this.isMatched) this.run("Matching students…", () => this.match());
    },

    /** The chosen assignment's grading type and points. Without an assignment, or points on it, the points are
     * `row`'s max marks. */
    assignmentGrading(row) {
      const { gradingType = "points", pointsPossible } = this.chosenAssignment ?? {};
      return { gradingType, pointsPossible: pointsPossible ?? row.maxMarks };
    },
    /** `row`'s `posted_grade` on the chosen assignment, with the send settings. */
    postedGrade(row) {
      const { mode, rounding } = this.settings;
      return gradeFor(row, {
        mode,
        rounding: Number(rounding),
        pointsPossible: this.assignmentGrading(row).pointsPossible,
      });
    },
    /** The grade Canvas shows for `row` on the chosen assignment. */
    canvasGrade(row) {
      const grade = this.postedGrade(row);
      return grade === null ? "—" : displayedGrade(grade, this.assignmentGrading(row));
    },

    /** Every row's student and what sending does for them, the ones to look at first. A matched student's
     * `currentGrade` and `sentGrade` are their grade in Canvas's gradebook now and once sent. */
    get students() {
      if (!this.submissions) return [];
      const inGradebook = (grade, row) => (grade == null ? null : gradebookGrade(grade, this.assignmentGrading(row)));
      const student = (row, user, status) => ({
        row,
        status,
        name: user?.sortable_name ?? "",
        marks: displayedGrade(row.marks, { gradingType: "points", pointsPossible: row.maxMarks }),
      });
      const matchedStudent = ({ row, user }) => {
        if (!this.chosenAssignment) return { ...student(row, user, "unchanged"), sentGrade: "—" };
        const submission = this.submissions.get(user.id);
        const sentGrade = inGradebook(this.postedGrade(row), row);
        const currentGrade = inGradebook(submission?.score, row);
        const addsComment =
          this.settings.feedback === "true" && Boolean(row.comment) && !submission?.comments.has(row.commentText);
        const isGradeChanged = currentGrade !== null && sentGrade !== currentGrade;
        const status = sentGrade !== currentGrade || addsComment ? "changed" : "unchanged";
        return {
          ...student(row, user, status),
          user,
          currentGrade,
          sentGrade: sentGrade ?? "—",
          isGradeChanged,
          addsComment,
        };
      };
      return [
        ...this.unmatched.map((row) => student(row, null, "not_in_canvas")),
        ...this.conflicts.flatMap(({ user, rows }) => rows.map((row) => student(row, user, "duplicate_id"))),
        ...this.matched.map(matchedStudent),
      ].sort(
        (a, b) =>
          STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
          b.row.unmarkedCount - a.row.unmarkedCount ||
          naturalOrder(a.row.studentId, b.row.studentId),
      );
    },
    get changes() {
      return this.students.filter((student) => student.status === "changed");
    },
    /** The filters with any students, and how many each has. */
    get filters() {
      const { students } = this;
      return [
        ...FILTERS.map(({ key, label, test }) => ({ key, label, count: students.filter(test).length })),
        { key: "only_in_canvas", label: "Only in Canvas", count: this.unmatchedUsers.length },
      ].filter(({ key, count }) => key === "all" || count);
    },
    /** The chosen filter, or All once it has no students. */
    get activeFilter() {
      return this.filters.some(({ key }) => key === this.filter) ? this.filter : "all";
    },
    set activeFilter(key) {
      this.filter = key;
    },
    get shownStudents() {
      const { test } = FILTERS.find(({ key }) => key === this.activeFilter) ?? FILTERS[0];
      return this.students.filter(
        (student) => test(student) && matches(`${student.row.studentId} ${student.name}`.toLowerCase(), this.query),
      );
    },
    get shownCanvasUsers() {
      return this.unmatchedUsers.filter((user) => matches(user.sortable_name.toLowerCase(), this.query));
    },
    get matchedNote() {
      return `${this.matched.length} of ${this.rowCount} matched in Canvas`;
    },
    get upToDate() {
      return Boolean(this.isStudentListReady && this.chosenAssignment && this.matched.length && !this.changes.length);
    },

    get canSend() {
      return Boolean(this.connected && this.chosenAssignment && this.changes.length && !this.isBusy);
    },
    get sending() {
      return this.busyLabel === SENDING;
    },
    get sendLabel() {
      if (this.sending) return "Sending…";
      if (!this.isStudentListReady || !this.matched.length) return "Send to Canvas";
      if (!this.chosenAssignment) return "Choose an assignment";
      const count = this.changes.length;
      return count ? `Send ${counted(count, "grade")}` : "Up to date";
    },
    get reviewTarget() {
      return `${this.chosenAssignment?.name} · ${this.config.courseName}`;
    },
    /** How many answers still need marking among the grades sent. */
    get unmarkedCount() {
      return this.changes.reduce((count, { row }) => count + row.unmarkedCount, 0);
    },
    get notSentCount() {
      return this.rowCount - this.matched.length;
    },
    get sameCount() {
      return this.matched.length - this.changes.length;
    },

    /** Shows the `index`th shown student in the preview, which its row's `data-dialog` opens. */
    previewStudent(index) {
      this.preview.rows = this.shownStudents.map(({ row }) => row);
      this.showPreview(index);
    },
    /** Shows the `index`th row's grade and comment as Canvas would; an index past either end does nothing. */
    showPreview(index) {
      const row = this.preview.rows[index];
      if (!row) return;
      const grade = this.canvasGrade(row);
      const gradeFlowMarks = displayedGrade(row.marks, { gradingType: "points", pointsPossible: row.maxMarks });
      const sendsFeedback = this.settings.feedback === "true";
      this.$refs.previewComment.innerHTML = sendsFeedback ? row.comment : "";
      this.$refs.previewComment.scrollTop = 0;
      Object.assign(this.preview, {
        index,
        studentId: row.studentId,
        grade,
        gradeFlowMarks: grade === gradeFlowMarks ? "" : gradeFlowMarks,
        needsMarking: row.unmarkedCount ? `${counted(row.unmarkedCount, "answer needs", "answers need")} marking` : "",
        commentPlaceholder: !sendsFeedback ? "Feedback isn't sent." : row.comment ? "" : "Nothing marked yet.",
      });
    },
    /** The assignment's name as students see it: the chosen one's, or else the assessment's. */
    get previewAssignmentName() {
      return this.chosenAssignment?.name ?? this.config.name;
    },
    get previewPosition() {
      return `${this.preview.index + 1} of ${this.preview.rows.length}`;
    },

    review() {
      Object.assign(this, { reviewing: true, error: "" });
      openDialog(document.getElementById("send-review"));
    },
    closeReview() {
      Object.assign(this, { reviewing: false, error: "" });
    },
    preventCloseWhileSending(event) {
      if (this.sending) event.preventDefault();
    },

    async send() {
      const { changes } = this;
      await this.run(SENDING, async () => {
        const assignment = this.chosenAssignment;
        const gradeData = Object.fromEntries(
          changes.map(({ row, user, addsComment }) => [
            user.id,
            {
              posted_grade: this.postedGrade(row),
              ...(addsComment && { text_comment: row.comment }),
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
        document.getElementById("send-review").close();
        showToast(`Sent ${counted(changes.length, "grade")} to “${assignment.name}”.`, "success");
        await htmx.ajax("POST", this.config.sentUrl, {
          source: this.$root,
          target: "#sent-marks",
          swap: "outerHTML",
          values: { assignment_name: assignment.name, student_count: changes.length },
        });
        this.submissions = await this.client.submissions(this.courseId, assignment.id);
      });
    },
  });
};
