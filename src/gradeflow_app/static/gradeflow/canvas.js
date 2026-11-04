// Canvas from the browser: the Canvas address and token never leave this device, and to send marks the app only
// gives encrypted IDs and totals (#publish-rows); IDs are decrypted and matched to Canvas users here.

import { canvasClient, gradeFor, matchStudents } from "./canvas-api.js";
import { decryptId } from "./crypto.js";
import { passphraseFor } from "./passphrase.js";
import { showToast } from "./toasts.js";
import { counted, matches } from "./words.js";

const CANVAS_STORAGE_KEY = "gradeflow:canvas";
const POLL_MS = 2000;
const SENDING = "Sending marks…";
const TOKEN_REFUSED = "Canvas didn't accept the access token.";

const errorMessage = (error, host) => {
  if (error.status === 401) return TOKEN_REFUSED;
  if (error.proxy) return `GradeFlow can't reach ${host} through its Canvas proxy. Check the Canvas address, or disconnect and connect again.`;
  if (error instanceof TypeError) return "Canvas can't be reached. Check your connection and try again.";
  return error.message || "Canvas can't be reached.";
};

// What every Canvas component shares: this device's connection to Canvas. A component's `load` fetches what it
// needs from Canvas, which also proves a new connection works.
const canvasComponent = () => ({
  canvasConnection: null,
  checking: false,
  baseUrl: "",
  token: "",
  busyLabel: "",
  error: "",

  get client() {
    return canvasClient({ ...this.canvasConnection, proxyUrl: this.config.proxy });
  },
  get isBusy() {
    return Boolean(this.busyLabel);
  },
  /** Connected once Canvas has accepted the token, not while it's being checked. */
  get connected() {
    return Boolean(this.canvasConnection) && !this.checking;
  },
  get canvasHost() {
    return this.canvasConnection ? new URL(this.canvasConnection.baseUrl).host : "";
  },

  async connect() {
    this.error = "";
    const baseUrl = URL.parse(this.baseUrl.trim())?.origin;
    if (!baseUrl) {
      this.error = "Enter your Canvas address, such as https://canvas.example.edu.";
      return;
    }
    if (!this.token.trim()) {
      this.error = "Paste an access token from Canvas.";
      return;
    }
    Object.assign(this, { canvasConnection: { baseUrl, token: this.token.trim() }, checking: true });
    const accepted = await this.load();
    this.checking = false;
    if (accepted) {
      localStorage.setItem(CANVAS_STORAGE_KEY, JSON.stringify(this.canvasConnection));
      this.token = "";
      showToast(`Connected to ${this.canvasHost}.`, "success");
    } else {
      this.canvasConnection = null;
      if (this.error !== TOKEN_REFUSED) this.error = `Couldn't connect to Canvas at ${baseUrl}. Check the address.`;
    }
  },
  disconnect() {
    localStorage.removeItem(CANVAS_STORAGE_KEY);
    this.canvasConnection = null;
  },

  async run(label, work) {
    this.busyLabel = label;
    this.error = "";
    try {
      return await work();
    } catch (error) {
      if (error.status === 401 && this.canvasConnection && !this.checking) this.disconnect();
      this.error = errorMessage(error, this.canvasHost);
      return null;
    } finally {
      this.busyLabel = "";
    }
  },
});

// Parts of a component, merged with their getters kept as getters.
const compose = (...parts) =>
  Object.defineProperties({}, Object.assign({}, ...parts.map(Object.getOwnPropertyDescriptors)));

const loadConnection = (component) => {
  component.config = JSON.parse(component.$el.dataset.config);
  try {
    component.canvasConnection = JSON.parse(localStorage.getItem(CANVAS_STORAGE_KEY));
  } catch {} // a connection that can't be read is none
};

const NO_CARD = Object.freeze({ id: "", name: "", detail: "", initial: "" });

/**
 * Choosing `this[key]` from `this[key + "Items"]` (each `{id, name, detail, initial, search}`) in a dialog, for each
 * of `keys`, shown by publish/picker.html. A component may give `<key>Placeholder`, the card while its items aren't
 * loaded or for something not yet made, and `afterChoose(key)`. `naming[key]` is true while the dialog names a new one.
 * With nothing chosen, the card is `NO_CARD`, so a template can read its fields without a check.
 */
const listPicker = (...keys) => {
  const dialog = (key) => document.getElementById(`${key}-picker`);
  return {
    queries: Object.fromEntries(keys.map((key) => [key, ""])),
    naming: Object.fromEntries(keys.map((key) => [key, false])),
    card(key) {
      return this[`${key}Items`].find((item) => item.id === this[key]) ?? this[`${key}Placeholder`] ?? NO_CARD;
    },
    shown(key) {
      return this[`${key}Items`]
        .filter((item) => matches(item.search, this.queries[key]))
        .map((item) => ({ ...item, selected: item.id === this[key] }));
    },
    loading(key) {
      return this.isBusy && !this[`${key}Items`].length;
    },
    pick(key) {
      this.queries[key] = "";
      this.naming[key] = false;
      dialog(key).showModal();
      dialog(key).querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
    },
    choose(key, event) {
      this[key] = event.target.closest("[data-id]").dataset.id;
      dialog(key).close();
      this.afterChoose?.(key);
    },
    close(key) {
      dialog(key).close();
    },
  };
};

const pickerItem = (id, name, detail = "") => ({
  id: String(id),
  name,
  detail,
  initial: name.charAt(0),
  search: `${name} ${detail}`.toLowerCase(),
});

// A Canvas course as the picker shows it: its code, term and size under its name, newest first (by its term's start,
// else its own, since many terms have none).
const describeCourse = (course) => {
  const students = course.total_students;
  const detail = [course.course_code, course.term?.name, students != null && counted(students, "student")];
  return { ...pickerItem(course.id, course.name, detail.filter(Boolean).join(" · ")), started: course.term?.start_at ?? course.start_at ?? course.created_at ?? "" };
};
const newestFirst = (a, b) => b.started.localeCompare(a.started) || a.name.localeCompare(b.name);

// An assignment group as the picker shows it, with its share of the final grade when the course weights its groups.
const describeGroup = (group, weighted) =>
  Object.assign(pickerItem(group.id, group.name, weighted ? `Worth ${group.group_weight}%` : ""), { weight: group.group_weight });

// Assignment groups: choosing one, or naming a new one, which is made in Canvas at once. In a course that weights its
// groups, a new one is given a share of the final grade: what the others leave, to start.
const groups = () => ({
  groupItems: [],
  group: "",
  weighted: false,
  groupNewName: "GradeFlow",
  groupNewWeight: 0,

  get canCreateGroup() {
    const weight = this.groupNewWeight;
    return Boolean(this.groupNewName.trim() && !this.isBusy && (!this.weighted || (Number.isFinite(weight) && weight >= 0)));
  },
  /** Within a `run`, so a page loads in one busy spell: x-show misses a hide when shown again in the same frame. */
  async loadGroups(preferred) {
    const [course, loadedGroups] = await Promise.all([this.client.course(this.course), this.client.assignmentGroups(this.course)]);
    this.weighted = course.apply_assignment_group_weights;
    this.groupItems = loadedGroups.map((group) => describeGroup(group, this.weighted));
    if (!this.groupItems.some((group) => group.id === this.group)) {
      this.group = this.groupItems.some((group) => group.id === preferred) ? preferred : (this.groupItems[0]?.id ?? "");
    }
  },
  newGroup() {
    const assignedWeight = this.groupItems.reduce((total, group) => total + group.weight, 0);
    this.naming.group = true;
    this.groupNewWeight = Math.max(0, Number((100 - assignedWeight).toFixed(10)));
  },
  async createGroup() {
    if (!this.canCreateGroup) return;
    const group = { name: this.groupNewName.trim(), ...(this.weighted && { group_weight: this.groupNewWeight }) };
    const createdGroup = await this.run("Creating the assignment group…", () => this.client.createAssignmentGroup(this.course, group));
    if (!createdGroup) return;
    this.groupItems.push(describeGroup(createdGroup, this.weighted));
    this.group = String(createdGroup.id);
    this.naming.group = false;
    this.close("group");
    showToast(`Created the assignment group “${createdGroup.name}” in Canvas.`, "success");
    this.afterChoose?.("group");
  },
});

// The Integrations page's Canvas card: connecting this device, which lists its courses to prove the token works.
export const canvasConnection = () =>
  compose(canvasComponent(), {
    init() {
      loadConnection(this);
    },
    load() {
      return this.run("Connecting…", async () => {
        await this.client.courses();
        return true;
      });
    },
  });

// A course's Canvas page: the Canvas course its marks go to, and the group new assignments go in. This device
// connects to Canvas on the Integrations page.
export const canvasCourse = () =>
  compose(canvasComponent(), listPicker("course", "group"), groups(), {
    courseItems: [],
    course: "",

    init() {
      loadConnection(this);
      Object.assign(this, { course: this.config.course, group: this.config.assignmentGroup });
      if (this.canvasConnection) this.load();
    },
    get coursePlaceholder() {
      return this.config.course && this.course === this.config.course ? pickerItem(this.course, this.config.courseName) : null;
    },
    get groupPlaceholder() {
      return this.config.assignmentGroup && this.group === this.config.assignmentGroup ? pickerItem(this.group, this.config.assignmentGroupName) : null;
    },
    load() {
      return this.run("Loading courses…", async () => {
        this.courseItems = (await this.client.courses()).map(describeCourse).sort(newestFirst);
        if (this.course) await this.loadGroups(this.config.assignmentGroup);
        return true;
      });
    },
    async afterChoose(key) {
      if (key === "course") {
        this.group = "";
        await this.run("Loading assignment groups…", () => this.loadGroups());
      }
    },
  });

// An assessment's Publish page: sending its marks to an assignment, existing or new, in the course's Canvas course,
// after a review that matches students. How they're sent is the server's to say (#send-settings), as its form saves it.
export const canvasPublisher = () => {
  let students = []; // the Canvas course's, once matched

  return compose(canvasComponent(), listPicker("assignment", "group"), groups(), {
    assignmentItems: [],
    assignment: "",
    newName: "",
    matched: [],
    unmatched: [],
    reviewing: false,
    isMatched: false,

    init() {
      loadConnection(this);
      Object.assign(this, { assignment: this.config.assignment, newName: this.config.name });
      if (this.canvasConnection) this.load();
    },
    get course() {
      return this.config.course;
    },
    get creating() {
      return this.assignment === "new";
    },
    /** The assignment as it's saved: none until a new one is made in Canvas. */
    get savedAssignment() {
      return this.creating ? "" : this.assignment;
    },
    get assignmentPlaceholder() {
      if (!this.creating) return null;
      const group = this.card("group").name;
      const detail = group ? `New, in ${group}` : "New";
      return { ...pickerItem("new", this.newName.trim() || "New assignment", detail), initial: "+" };
    },
    get chosenAssignment() {
      return this.assignmentItems.find((assignment) => assignment.id === this.assignment);
    },
    get canReview() {
      return Boolean(this.assignment && !this.isBusy && (!this.creating || this.newName.trim()));
    },
    get canSend() {
      return this.isMatched && this.matched.length > 0 && !this.isBusy;
    },
    get sending() {
      return this.busyLabel === SENDING;
    },
    get sendLabel() {
      if (this.sending) return "Sending…";
      return this.matched.length ? `Send ${counted(this.matched.length, "student's", "students'")} marks` : "Send marks";
    },
    get reviewTarget() {
      return `${this.card("assignment").name} · ${this.config.courseName}`;
    },
    /** Says so when marks are scaled to an assignment worth a different number of points. */
    get scaling() {
      const maxMarks = this.matched[0]?.row.maxMarks;
      const pointsPossible = this.chosenAssignment?.pointsPossible;
      if (document.getElementById("send-settings").dataset.mode !== "marks" || !pointsPossible || !maxMarks || pointsPossible === maxMarks) return "";
      return `Totals out of ${maxMarks} are scaled to the assignment's ${counted(pointsPossible, "mark")}.`;
    },

    load() {
      return this.run("Loading assignments…", async () => {
        const loadedAssignments = await this.client.assignments(this.course);
        await this.loadGroups(this.config.assignmentGroup);
        const groupName = (id) => this.groupItems.find((group) => group.id === String(id))?.name;
        this.assignmentItems = loadedAssignments.map((assignment) =>
          Object.assign(pickerItem(assignment.id, assignment.name, [groupName(assignment.assignment_group_id), assignment.points_possible != null && counted(assignment.points_possible, "mark")].filter(Boolean).join(" · ")), {
            pointsPossible: assignment.points_possible,
          }),
        );
        return true;
      });
    },
    afterChoose(key) {
      if (key === "assignment") this.changedAssignment();
    },
    newAssignment() {
      this.assignment = "new";
      this.close("assignment");
      this.changedAssignment();
    },
    changedAssignment() {
      this.$nextTick(() => this.$refs.sendSettings.dispatchEvent(new Event("change")));
    },

    /** The marks as they are now, matched to the Canvas course's students. */
    async match() {
      const rows = JSON.parse(document.getElementById("publish-rows-data").textContent);
      if (!rows.length) throw new Error("There are no marks to send yet.");
      const passphrase = await passphraseFor(this.config.assessment, rows[0].studentId);
      if (!passphrase) throw new Error("Enter the student ID passphrase to match students.");
      const decryptedRows = await Promise.all(rows.map(async (row) => ({ ...row, studentId: await decryptId(row.studentId, passphrase) })));
      Object.assign(this, matchStudents(decryptedRows, students));
    },
    async review() {
      Object.assign(this, { reviewing: true, isMatched: false, error: "" });
      const dialog = document.getElementById("send-review");
      if (!dialog.open) dialog.showModal();
      await this.run("Matching students…", async () => {
        students = await this.client.students(this.course);
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
        const comments = feedback === "true" ? await this.client.comments(this.course, assignment.id) : new Map();
        const text = (html) => new DOMParser().parseFromString(html, "text/html").body.textContent;
        const gradeData = Object.fromEntries(
          this.matched.map(({ row, user }) => [
            user.id,
            {
              posted_grade: gradeFor(row, { mode, rounding: Number(rounding), pointsPossible: assignment.pointsPossible ?? row.maxMarks }),
              ...(feedback === "true" && row.comment && !comments.get(user.id)?.has(text(row.comment)) ? { text_comment: row.comment } : {}),
            },
          ]),
        );
        let progress = await this.client.updateGrades(this.course, assignment.id, gradeData);
        while (progress.workflow_state === "queued" || progress.workflow_state === "running") {
          await new Promise((resolve) => setTimeout(resolve, POLL_MS));
          progress = await this.client.progress(progress.id);
        }
        if (progress.workflow_state !== "completed") throw new Error(progress.message || "Canvas couldn't update the grades.");
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
      const created = await this.client.createAssignment(this.course, { name: this.newName.trim(), points_possible: maxMarks, assignment_group_id: this.group });
      const assignment = { ...pickerItem(created.id, created.name), pointsPossible: created.points_possible };
      this.assignmentItems.push(assignment);
      this.assignment = assignment.id;
      this.changedAssignment();
      return assignment;
    },
  });
};
