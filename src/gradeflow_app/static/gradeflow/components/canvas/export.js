// The Export page: the assessment's students matched to the Canvas course's, each with their grade in Canvas and the
// grade to send. The server re-renders #send-settings and #export-rows-data; each runs its `x-init` when swapped in.

import { decryptId } from "../../lib/assessment-crypto.js";
import { gradebookGrade, gradeFor, matchStudents, studentViewGrade } from "../../lib/canvas-client.js";
import { counted, matches, naturalOrder } from "../../lib/words.js";
import { assessmentKeysFor, missingCourseKeyMessage } from "../../services/course-keys.js";
import { openDialog } from "../../services/dialogs.js";
import { readJson } from "../../services/page-data.js";
import { showToast } from "../../services/toasts.js";
import { assignmentPickerMixin, marksLabel } from "./assignment-picker.js";
import { connectionMixin, mergeMixins, readConfig } from "./connection.js";

const POLL_MS = 2000;
const POLL_LIMIT_MS = 5 * 60_000;

const FILTERS = [
  { key: "all", label: "All", test: () => true },
  { key: "to_send", label: "To send", test: (student) => student.status === "to_send" },
  { key: "needs_marking", label: "Needs marking", test: (student) => student.row.unmarkedCount > 0 },
  { key: "not_in_canvas", label: "Not in Canvas", test: (student) => student.status === "not_in_canvas" },
  { key: "duplicate_id", label: "Duplicate ID", test: (student) => student.status === "duplicate_id" },
];

/** A comment's text, as Canvas lists the comments it has. */
const commentText = (html) => new DOMParser().parseFromString(html, "text/html").body.textContent;

export const canvasExport = () => {
  let canvasStudents = [];
  let cachedStudents = null; // the last `students`, with what they depended on

  return mergeMixins(connectionMixin(), assignmentPickerMixin(), {
    sendSettings: {},
    rows: [], // the marks to send, their student IDs decrypted, in order of student ID
    matches: [],
    unmatchedRows: [],
    duplicates: [],
    unmatchedUsers: [],
    isMatched: false,
    submissions: null, // the chosen assignment's, by user ID; null until they load
    chosenFilter: "all",
    studentQuery: "",
    sendRequestState: { busyLabel: "", error: "" },
    preview: { students: [], index: 0 },

    async init() {
      this.config = readConfig(this.$el);
      this.assignmentId = this.config.assignment;
      await this.loadConnection();
      if (this.canvasConnection) this.load();
    },
    get maxMarks() {
      return this.rows[0]?.maxMarks ?? 0;
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
      const assignment = this.chosenAssignment;
      const pointsPossible = assignment?.pointsPossible;
      const group = this.groups.find((candidate) => candidate.assignments.includes(assignment));
      const scaled =
        this.sendSettings.mode === "marks" && pointsPossible && this.maxMarks && pointsPossible !== this.maxMarks;
      return [group?.name, marksLabel(pointsPossible), scaled && `scaled from ${this.maxMarks}`]
        .filter(Boolean)
        .join(" · ");
    },
    get isLoadingAssignments() {
      return this.isBusy && !this.groups.length;
    },

    readSendSettings() {
      const { mode, rounding, feedback } = document.getElementById("send-settings").dataset;
      this.sendSettings = { mode, rounding: Number(rounding), feedback: feedback === "true" };
    },

    /** Decrypts the rows and loads the Canvas students, the assignments and the chosen assignment's submissions.
     * Without a saved assignment, chooses the one named after the assessment. */
    load() {
      return this.run("Loading the Canvas course…", async () => {
        const savedId = this.assignmentId;
        const savedSubmissionsRequest = savedId ? this.client.submissions(this.courseId, savedId) : null;
        savedSubmissionsRequest?.catch(() => {}); // left unawaited if Canvas no longer has the saved assignment
        const course = this.client.course(this.courseId);
        [canvasStudents] = await Promise.all([
          course.then(({ total_students }) => this.client.students(this.courseId, total_students)),
          this.loadGroups(course).then(() => {
            if (!this.chosenAssignment)
              this.assignmentId = this.assignments.find(({ name }) => name === this.config.name)?.id ?? "";
            return this.loadSubmissions(this.assignmentId === savedId ? savedSubmissionsRequest : null);
          }),
          this.decryptRows().then((rows) => {
            this.rows = rows;
          }),
        ]);
        this.match();
        return true;
      });
    },
    /** The chosen assignment's submissions, from `request` if it's already been made; none without an assignment.
     * The previous assignment's stay until they load. */
    async loadSubmissions(request) {
      const id = this.assignmentId;
      const submissions = id ? await (request ?? this.client.submissions(this.courseId, id)) : new Map();
      if (this.assignmentId === id) this.submissions = submissions;
    },
    /** Saves the assignment `id` as the chosen one and loads its submissions. */
    changeAssignment(id) {
      this.assignmentId = id;
      this.$nextTick(() => document.getElementById("send-settings-form").dispatchEvent(new Event("change")));
      this.run("Loading grades…", () => this.loadSubmissions());
    },

    /** The marks as the server last rendered them, their student IDs decrypted, in order of student ID. */
    async decryptRows() {
      const assessmentElement = this.$root.closest("[data-assessment]");
      const keys = await assessmentKeysFor(assessmentElement);
      if (!keys) throw new Error(missingCourseKeyMessage(assessmentElement));
      const rows = await Promise.all(
        readJson("export-rows-data").map(async (row) => ({
          ...row,
          studentId: await decryptId(row.studentId, keys),
          commentText: commentText(row.comment),
          marksText: studentViewGrade(row.marks, { gradingType: "points", pointsPossible: row.maxMarks }),
          needsMarkingLabel: `${counted(row.unmarkedCount, "needs", "need")} marking`,
        })),
      );
      return rows.sort((a, b) => naturalOrder(a.studentId, b.studentId));
    },
    match() {
      Object.assign(this, matchStudents(this.rows, canvasStudents), { isMatched: true });
    },
    /** Once the students are matched and the chosen assignment's submissions have loaded. */
    get isCanvasLoaded() {
      return this.isMatched && Boolean(this.submissions);
    },
    /** Decrypts and matches the marks again once they've refreshed. */
    rematch() {
      if (!this.rows.length) return;
      this.run("Matching students…", async () => {
        const rows = await this.decryptRows();
        this.rows = rows;
        if (this.isMatched) this.match();
      });
    },

    /** The chosen assignment's grading type and points. Without an assignment, or points on it, the points are the
     * max marks. */
    get grading() {
      const { gradingType = "points", pointsPossible } = this.chosenAssignment ?? {};
      return { gradingType, pointsPossible: pointsPossible ?? this.maxMarks };
    },
    /** `row`'s `posted_grade` on the chosen assignment, with the send settings. */
    postedGrade(row) {
      return gradeFor(row, { ...this.sendSettings, pointsPossible: this.grading.pointsPossible });
    },

    /** Every row's student, in order of student ID, and what sending does for them: "loading" until Canvas has
     * loaded. A matched student's `currentGrade` and `gradeToSend` are their grade in Canvas's gradebook now and once
     * sent. Listed again only when what they depend on changes. */
    get students() {
      const { rows, matches, submissions, sendSettings, chosenAssignment } = this;
      const { id, pointsPossible, gradingType } = chosenAssignment ?? {};
      const dependencies = [rows, matches, submissions, sendSettings, id, pointsPossible, gradingType];
      if (cachedStudents?.dependencies.every((dependency, i) => dependency === dependencies[i]))
        return cachedStudents.students;
      cachedStudents = { dependencies, students: this.listStudents() };
      return cachedStudents.students;
    },
    listStudents() {
      const { grading } = this;
      const gradebookGradeOf = (grade) => (grade == null ? null : gradebookGrade(grade, grading));
      const student = (row, user, status) => ({ row, status, name: user?.sortable_name ?? "" });
      const matchedStudent = ({ row, user }) => {
        if (!this.chosenAssignment) return { ...student(row, user, "up_to_date"), gradeToSend: "—" };
        const submission = this.submissions.get(user.id);
        const gradeToSend = gradebookGradeOf(this.postedGrade(row));
        const currentGrade = gradebookGradeOf(submission?.score);
        const addsComment =
          this.sendSettings.feedback && Boolean(row.comment) && !submission?.comments.has(row.commentText);
        const status = gradeToSend !== currentGrade || addsComment ? "to_send" : "up_to_date";
        return {
          ...student(row, user, status),
          user,
          currentGrade,
          gradeToSend: gradeToSend ?? "—",
          isGradeChanged: currentGrade !== null && gradeToSend !== currentGrade,
          addsComment,
        };
      };
      if (!this.isCanvasLoaded) return this.rows.map((row) => student(row, null, "loading"));
      const studentsById = new Map(
        [
          ...this.unmatchedRows.map((row) => student(row, null, "not_in_canvas")),
          ...this.duplicates.flatMap(({ user, rows }) => rows.map((row) => student(row, user, "duplicate_id"))),
          ...this.matches.map(matchedStudent),
        ].map((entry) => [entry.row.studentId, entry]),
      );
      return this.rows.map((row) => studentsById.get(row.studentId));
    },
    get studentsToSend() {
      return this.students.filter((student) => student.status === "to_send");
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
      return this.filters.some(({ key }) => key === this.chosenFilter) ? this.chosenFilter : "all";
    },
    set activeFilter(key) {
      this.chosenFilter = key;
    },
    get shownStudents() {
      const { test } = FILTERS.find(({ key }) => key === this.activeFilter) ?? FILTERS[0];
      return this.students.filter(
        (student) =>
          test(student) && matches(`${student.row.studentId} ${student.name}`.toLowerCase(), this.studentQuery),
      );
    },
    get shownCanvasUsers() {
      return this.unmatchedUsers.filter((user) => matches(user.sortable_name.toLowerCase(), this.studentQuery));
    },
    get isUpToDate() {
      return Boolean(
        this.isCanvasLoaded && this.chosenAssignment && this.matches.length && !this.studentsToSend.length,
      );
    },

    get isSending() {
      return Boolean(this.sendRequestState.busyLabel);
    },
    get canSend() {
      return Boolean(
        this.connected && this.chosenAssignment && this.studentsToSend.length && !this.isBusy && !this.isSending,
      );
    },
    get sendLabel() {
      if (!this.isCanvasLoaded || !this.matches.length) return "Send to Canvas";
      if (!this.chosenAssignment) return "Choose an assignment";
      const count = this.studentsToSend.length;
      return count ? `Send ${counted(count, "grade")}` : "Up to date";
    },
    get reviewSubtitle() {
      return `${this.chosenAssignment?.name} · ${this.config.courseName}`;
    },
    /** How many answers still need marking among the grades to send. */
    get unmarkedCount() {
      return this.studentsToSend.reduce((count, { row }) => count + row.unmarkedCount, 0);
    },
    get notSentCount() {
      return this.rows.length - this.matches.length;
    },
    get upToDateCount() {
      return this.matches.length - this.studentsToSend.length;
    },

    /** Previews the `index`th shown student; their row's `data-dialog` opens the preview. */
    previewStudent(index) {
      this.preview.students = this.shownStudents;
      this.showPreview(index);
    },
    /** Shows the `index`th previewed student's comment as Canvas would; an index past either end does nothing. */
    showPreview(index) {
      const student = this.preview.students[index];
      if (!student) return;
      this.preview.index = index;
      this.$refs.previewComment.innerHTML = this.sendSettings.feedback ? student.row.comment : "";
      this.$refs.previewComment.scrollTop = 0;
    },
    /** The previewed student's ID, their grade as they see it in Canvas, GradeFlow's marks when they differ, and
     * notes on what's missing. */
    get previewDetails() {
      const row = this.preview.students[this.preview.index]?.row;
      if (!row) return { studentId: "", grade: "", marks: "", needsMarkingLabel: "", commentNote: "" };
      const postedGrade = this.postedGrade(row);
      const grade = postedGrade === null ? "—" : studentViewGrade(postedGrade, this.grading);
      return {
        studentId: row.studentId,
        grade,
        marks: grade === row.marksText ? "" : row.marksText,
        needsMarkingLabel: row.unmarkedCount
          ? `${counted(row.unmarkedCount, "answer needs", "answers need")} marking`
          : "",
        commentNote: !this.sendSettings.feedback ? "Feedback isn't sent." : row.comment ? "" : "Nothing marked yet.",
      };
    },
    /** The assignment's name as students see it: the chosen one's, or else the assessment's. */
    get previewAssignmentName() {
      return this.chosenAssignment?.name ?? this.config.name;
    },

    openReview() {
      this.sendRequestState.error = "";
      openDialog(document.getElementById("send-review"));
    },
    preventCloseWhileSending(event) {
      if (this.isSending) event.preventDefault();
    },

    /** Sends the grades to send. Canvas updates them in a background job; the page stays usable but can't be left
     * until the job ends. */
    async send() {
      const { studentsToSend } = this;
      const assignment = this.chosenAssignment;
      const gradeData = Object.fromEntries(
        studentsToSend.map(({ row, user, addsComment }) => [
          user.id,
          {
            posted_grade: this.postedGrade(row),
            ...(addsComment && { text_comment: row.comment }),
          },
        ]),
      );
      const gradesLabel = counted(studentsToSend.length, "grade");
      await this.run(
        `Canvas is updating ${gradesLabel}…`,
        async () => {
          let progress = await this.client.updateGrades(this.courseId, assignment.id, gradeData);
          document.getElementById("send-review").close();
          const pollDeadline = Date.now() + POLL_LIMIT_MS;
          while (progress.workflow_state === "queued" || progress.workflow_state === "running") {
            if (Date.now() > pollDeadline)
              throw new Error("Canvas is still updating the grades. Check the assignment in Canvas.");
            await new Promise((resolve) => setTimeout(resolve, POLL_MS));
            progress = await this.client.progress(progress.id);
          }
          if (progress.workflow_state !== "completed")
            throw new Error(progress.message || "Canvas couldn't update the grades.");
          showToast(`Sent ${gradesLabel} to “${assignment.name}”.`, "success");
          await htmx.ajax("POST", this.config.sentUrl, {
            source: this.$root,
            target: "#sent-marks",
            swap: "outerHTML",
            values: { assignment_name: assignment.name, student_count: studentsToSend.length },
          });
          await this.loadSubmissions();
        },
        this.sendRequestState,
      );
    },
    preventUnloadWhileUpdating(event) {
      if (this.isSending) event.preventDefault();
    },
    preventNavigationWhileUpdating(event) {
      if (!this.isSending || event.detail.ctx.request.headers["HX-Request-Type"] !== "full") return;
      event.preventDefault();
      showToast("Stay on this page until Canvas has updated the grades.", "info");
    },
  });
};
