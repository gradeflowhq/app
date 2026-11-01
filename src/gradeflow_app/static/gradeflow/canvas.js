// Canvas from the browser: the Canvas address and token never leave this device, and to send marks the app only
// gives encrypted IDs and totals (#publish-rows); IDs are decrypted and matched to Canvas users here.

import { canvasClient, gradeFor, matchStudents } from "./canvas-api.js";
import { decryptId } from "./crypto.js";
import { passphraseFor } from "./passphrase.js";
import { local } from "./storage.js";
import { report } from "./toasts.js";
import { matches, plural } from "./words.js";

const CONNECTION = "gradeflow:canvas";
const POLL_MS = 2000;
const SENDING = "Sending marks…";
const TOKEN_REFUSED = "Canvas didn't accept the access token.";

const problem = (error, host) => {
  if (error.status === 401) return TOKEN_REFUSED;
  if (error.proxy) return `GradeFlow can't reach ${host} through its Canvas proxy. Check the Canvas address, or disconnect and connect again.`;
  if (error instanceof TypeError) return "Canvas can't be reached. Check your connection and try again.";
  return error.message || "Canvas can't be reached.";
};

// What every Canvas component shares: this device's connection to Canvas. A component's `load` fetches what it
// needs from Canvas, which also proves a new connection works.
const connection = () => ({
  connection: null,
  checking: false,
  baseUrl: "",
  token: "",
  busy: "",
  error: "",

  get client() {
    return canvasClient({ ...this.connection, proxyUrl: this.config.proxy });
  },
  get working() {
    return Boolean(this.busy);
  },
  /** Connected once Canvas has accepted the token, not while it's being checked. */
  get connected() {
    return Boolean(this.connection) && !this.checking;
  },
  get canvasHost() {
    return this.connection ? new URL(this.connection.baseUrl).host : "";
  },

  async connect() {
    this.error = "";
    let baseUrl;
    try {
      baseUrl = new URL(this.baseUrl.trim()).origin;
    } catch {
      this.error = "Enter your Canvas address, such as https://canvas.example.edu.";
      return;
    }
    if (!this.token.trim()) {
      this.error = "Paste an access token from Canvas.";
      return;
    }
    Object.assign(this, { connection: { baseUrl, token: this.token.trim() }, checking: true });
    const accepted = await this.load();
    this.checking = false;
    if (accepted) {
      local.set(CONNECTION, JSON.stringify(this.connection));
      this.token = "";
      report(`Connected to ${this.canvasHost}.`, "success");
    } else {
      this.connection = null;
      if (this.error !== TOKEN_REFUSED) this.error = `Couldn't connect to Canvas at ${baseUrl}. Check the address.`;
    }
  },
  disconnect() {
    local.remove(CONNECTION);
    this.connection = null;
  },

  async run(label, work) {
    this.busy = label;
    this.error = "";
    try {
      return await work();
    } catch (error) {
      if (error.status === 401 && this.connection && !this.checking) this.disconnect();
      this.error = problem(error, this.canvasHost);
      return null;
    } finally {
      this.busy = "";
    }
  },
});

// Parts of a component, merged with their getters kept as getters.
const compose = (...parts) =>
  Object.defineProperties({}, Object.assign({}, ...parts.map(Object.getOwnPropertyDescriptors)));

const startConnected = (component) => {
  component.config = JSON.parse(component.$el.dataset.config);
  component.connection = local.json(CONNECTION);
};

/**
 * Choosing `this[key]` from `this[key + "Items"]` (each `{id, name, detail, initial, search}`) in a dialog, shown by
 * publish/picker.html. A component may give `<key>Placeholder`, the card while its items aren't loaded or for
 * something not yet made; `<key>Creating`, while the dialog names a new one; and `chose(key)`.
 */
const listPicker = (key) => {
  const Key = key[0].toUpperCase() + key.slice(1);
  const dialog = () => document.getElementById(`${key}-picker`);
  return {
    [`${key}Query`]: "",
    [`${key}Creating`]: false,
    get [`${key}Card`]() {
      return this[`${key}Items`].find((item) => item.id === this[key]) ?? this[`${key}Placeholder`] ?? null;
    },
    get [`${key}Name`]() {
      return this[`${key}Card`]?.name ?? "";
    },
    get [`${key}Detail`]() {
      return this[`${key}Card`]?.detail ?? "";
    },
    get [`${key}Initial`]() {
      return this[`${key}Card`]?.initial ?? "";
    },
    get [`${key}Shown`]() {
      return this[`${key}Items`]
        .filter((item) => matches(item.search, this[`${key}Query`]))
        .map((item) => ({ ...item, selected: item.id === this[key] }));
    },
    get [`${key}NoMatch`]() {
      return this[`${key}Items`].length > 0 && !this[`${key}Shown`].length;
    },
    get [`${key}Loading`]() {
      return this.working && !this[`${key}Items`].length;
    },
    [`pick${Key}`]() {
      Object.assign(this, { [`${key}Query`]: "", [`${key}Creating`]: false });
      dialog().showModal();
      dialog().querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
    },
    [`choose${Key}`](event) {
      this[key] = event.target.closest("[data-id]").dataset.id;
      dialog().close();
      this.chose?.(key);
    },
    [`close${Key}`]() {
      dialog().close();
    },
  };
};

const item = (id, name, detail = "") => ({
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
  const detail = [course.course_code, course.term?.name, students != null && plural(students, "student")];
  return { ...item(course.id, course.name, detail.filter(Boolean).join(" · ")), started: course.term?.start_at ?? course.start_at ?? course.created_at ?? "" };
};
const newestFirst = (a, b) => b.started.localeCompare(a.started) || a.name.localeCompare(b.name);

// An assignment group as the picker shows it, with its share of the final grade when the course weights its groups.
const describeGroup = (group, weighted) =>
  Object.assign(item(group.id, group.name, weighted ? `Worth ${group.group_weight}%` : ""), { weight: group.group_weight });

// Assignment groups: choosing one, or naming a new one, which is made in Canvas at once. In a course that weights its
// groups, a new one is given a share of the final grade: what the others leave, to start.
const groups = () => ({
  groups: [],
  group: "",
  weighted: false,
  groupNewName: "GradeFlow",
  groupNewWeight: 0,

  get groupItems() {
    return this.groups;
  },
  get canCreateGroup() {
    const weight = this.groupNewWeight;
    return Boolean(this.groupNewName.trim() && !this.busy && (!this.weighted || (Number.isFinite(weight) && weight >= 0)));
  },
  /** Within a `run`, so a page loads in one busy spell: x-show misses a hide when shown again in the same frame. */
  async loadGroups(preferred) {
    const [course, found] = await Promise.all([this.client.course(this.course), this.client.assignmentGroups(this.course)]);
    this.weighted = course.apply_assignment_group_weights;
    this.groups = found.map((each) => describeGroup(each, this.weighted));
    if (!this.groups.some((each) => each.id === this.group)) {
      this.group = this.groups.some((each) => each.id === preferred) ? preferred : (this.groups[0]?.id ?? "");
    }
  },
  newGroup() {
    const given = this.groups.reduce((total, each) => total + each.weight, 0);
    Object.assign(this, { groupCreating: true, groupNewWeight: Math.max(0, Number((100 - given).toFixed(10))) });
  },
  cancelGroup() {
    this.groupCreating = false;
  },
  async createGroup() {
    if (!this.canCreateGroup) return;
    const group = { name: this.groupNewName.trim(), ...(this.weighted && { group_weight: this.groupNewWeight }) };
    const made = await this.run("Creating the assignment group…", () => this.client.createAssignmentGroup(this.course, group));
    if (!made) return;
    this.groups.push(describeGroup(made, this.weighted));
    Object.assign(this, { group: String(made.id), groupCreating: false });
    this.closeGroup();
    report(`Created the assignment group “${made.name}” in Canvas.`, "success");
    this.chose?.("group");
  },
});

// The Integrations page's Canvas card: connecting this device, which lists its courses to prove the token works.
export const canvasConnection = () =>
  compose(connection(), {
    init() {
      startConnected(this);
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
  compose(connection(), listPicker("course"), listPicker("group"), groups(), {
    courses: [],
    course: "",

    init() {
      startConnected(this);
      Object.assign(this, { course: this.config.course, group: this.config.group });
      if (this.connection) this.load();
    },
    get courseItems() {
      return this.courses;
    },
    get coursePlaceholder() {
      return this.config.course && this.course === this.config.course ? item(this.course, this.config.courseName) : null;
    },
    get groupPlaceholder() {
      return this.config.group && this.group === this.config.group ? item(this.group, this.config.groupName) : null;
    },
    load() {
      return this.run("Loading courses…", async () => {
        this.courses = (await this.client.courses()).map(describeCourse).sort(newestFirst);
        if (this.course) await this.loadGroups(this.config.group);
        return true;
      });
    },
    async chose(key) {
      if (key === "course") {
        this.group = "";
        await this.run("Loading assignment groups…", () => this.loadGroups());
      }
    },
  });

// An assessment's Publish page: sending its marks to an assignment, existing or new, in the course's Canvas course,
// after a review that matches students. How they're sent is the server's to say (#sending), as its form saves it.
export const canvasPublisher = () => {
  let students = []; // the Canvas course's, once checked

  return compose(connection(), listPicker("assignment"), listPicker("group"), groups(), {
    assignments: [],
    assignment: "",
    newName: "",
    matched: [],
    unmatched: [],
    reviewing: false,
    checked: false,

    init() {
      startConnected(this);
      Object.assign(this, { assignment: this.config.assignment, newName: this.config.name });
      if (this.connection) this.load();
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
    get assignmentItems() {
      return this.assignments;
    },
    get assignmentPlaceholder() {
      if (!this.creating) return null;
      const where = this.groupCard ? `New, in ${this.groupCard.name}` : "New";
      return { ...item("new", this.newName.trim() || "New assignment", where), initial: "+" };
    },
    get chosen() {
      return this.assignments.find((each) => each.id === this.assignment);
    },
    get canReview() {
      return Boolean(this.assignment && !this.busy && (!this.creating || this.newName.trim()));
    },
    get canSend() {
      return this.checked && this.matched.length > 0 && !this.busy;
    },
    get sending() {
      return this.busy === SENDING;
    },
    get sendLabel() {
      if (this.sending) return "Sending…";
      return this.matched.length ? `Send ${plural(this.matched.length, "student's", "students'")} marks` : "Send marks";
    },
    get reviewTarget() {
      return `${this.assignmentName} · ${this.config.courseName}`;
    },
    /** Says so when marks are scaled to an assignment worth a different number of points. */
    get scaling() {
      const maxMarks = this.matched[0]?.row.maxMarks;
      const possible = this.chosen?.possible;
      if (document.getElementById("sending").dataset.mode !== "marks" || !possible || !maxMarks || possible === maxMarks) return "";
      return `Totals out of ${maxMarks} are scaled to the assignment's ${plural(possible, "mark")}.`;
    },

    load() {
      return this.run("Loading assignments…", async () => {
        const found = await this.client.assignments(this.course);
        await this.loadGroups(this.config.group);
        const groupName = (id) => this.groups.find((each) => each.id === String(id))?.name;
        this.assignments = found.map((each) =>
          Object.assign(item(each.id, each.name, [groupName(each.assignment_group_id), each.points_possible != null && plural(each.points_possible, "mark")].filter(Boolean).join(" · ")), {
            possible: each.points_possible,
          }),
        );
        return true;
      });
    },
    chose(key) {
      if (key === "assignment") this.changedAssignment();
    },
    newAssignment() {
      this.assignment = "new";
      this.closeAssignment();
      this.changedAssignment();
    },
    changedAssignment() {
      this.$nextTick(() => this.$refs.sending.dispatchEvent(new Event("change")));
    },

    /** The marks as they are now, matched to the Canvas course's students. */
    async match() {
      const rows = JSON.parse(document.getElementById("publish-rows-data").textContent);
      if (!rows.length) throw new Error("There are no marks to send yet.");
      const passphrase = await passphraseFor(this.config.assessment, rows[0].studentId);
      if (!passphrase) throw new Error("Enter the student ID passphrase to match students.");
      const plain = await Promise.all(rows.map(async (row) => ({ ...row, studentId: await decryptId(row.studentId, passphrase) })));
      Object.assign(this, matchStudents(plain, students));
    },
    async review() {
      Object.assign(this, { reviewing: true, checked: false, error: "" });
      const dialog = document.getElementById("send-review");
      if (!dialog.open) dialog.showModal();
      await this.run("Matching students…", async () => {
        students = await this.client.students(this.course);
        await this.match();
        this.checked = true;
      });
    },

    closeReview() {
      Object.assign(this, { reviewing: false, error: "" });
    },
    holdWhileSending(event) {
      if (this.sending) event.preventDefault();
    },

    async send() {
      const { mode, rounding, feedback } = document.getElementById("sending").dataset;
      await this.run(SENDING, async () => {
        await this.match(); // marks may have changed since they were checked
        const assignment = await this.target();
        // Canvas adds a comment on every send, so nobody gets the same one twice. It lists comments as their text.
        const comments = feedback === "true" ? await this.client.comments(this.course, assignment.id) : new Map();
        const text = (html) => new DOMParser().parseFromString(html, "text/html").body.textContent;
        const gradeData = Object.fromEntries(
          this.matched.map(({ row, user }) => [
            user.id,
            {
              posted_grade: gradeFor(row, { mode, rounding: Number(rounding), possible: assignment.possible ?? row.maxMarks }),
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
        report(`Sent ${plural(sent, "student's", "students'")} marks to “${assignment.name}”.`, "success");
        await htmx.ajax("POST", this.config.sentUrl, {
          source: this.$root,
          target: "#publications",
          swap: "outerHTML",
          values: { assignment_name: assignment.name, student_count: sent },
        });
      });
    },

    async target() {
      if (!this.creating) return this.chosen;
      const maxMarks = this.matched[0]?.row.maxMarks;
      const created = await this.client.createAssignment(this.course, { name: this.newName.trim(), points_possible: maxMarks, assignment_group_id: this.group });
      const assignment = { ...item(created.id, created.name), possible: created.points_possible };
      this.assignments.push(assignment);
      this.assignment = assignment.id;
      this.changedAssignment();
      return assignment;
    },
  });
};
