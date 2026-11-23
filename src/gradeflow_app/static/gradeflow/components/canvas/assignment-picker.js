// Choosing the Canvas course's assignment in the dialog #assignment-picker (exports/assignment_picker.html): a card
// per assignment group with its assignments. Groups and assignments are created, edited and deleted in Canvas at once.
// The component it's mixed into has `courseId`, `maxMarks`, `config.name` and `choseAssignment()`, called once
// `assignmentId` changes.

import { counted, matches } from "../../lib/words.js";
import { openDialog } from "../../services/dialogs.js";

const EMPTY_FORM = Object.freeze({ kind: "", id: "", groupId: "", name: "", points: "", weight: "" });

const describeAssignment = (assignment) => ({
  id: String(assignment.id),
  name: assignment.name,
  pointsPossible: assignment.points_possible,
  gradingType: assignment.grading_type,
  published: assignment.published,
  graded: assignment.graded_submissions_exist,
  url: assignment.html_url,
});

const describeGroup = (group) => ({
  id: String(group.id),
  name: group.name,
  weight: group.group_weight,
  assignments: (group.assignments ?? []).map(describeAssignment),
});

/** An assignment's points as marks, if it has any. */
export const marksLabel = (points) => (points == null ? "" : counted(points, "mark"));

export const assignmentPickerMixin = () => ({
  assignmentId: "",
  groups: [],
  appliesGroupWeights: false,
  assignmentQuery: "",
  selectedAssignmentId: "",
  /** The busy label and error of the picker's Canvas requests, apart from the page's. */
  pickerStatus: { busyLabel: "", error: "" },
  /** The open form, for an assignment or a group (`kind`). A new one has no `id`; a new assignment goes in the group
   * `groupId`. */
  form: { ...EMPTY_FORM },
  /** The group asked to be deleted, and the group its assignments move to; none deletes them with it. */
  groupToDelete: { id: "", moveAssignmentsTo: "" },

  get assignments() {
    return this.groups.flatMap((group) => group.assignments);
  },
  get chosenAssignment() {
    return this.assignments.find((assignment) => assignment.id === this.assignmentId);
  },
  get chosenGroup() {
    return this.groups.find((group) => group.assignments.includes(this.chosenAssignment));
  },
  get isPickerBusy() {
    return Boolean(this.pickerStatus.busyLabel);
  },
  get selectedAssignment() {
    return this.assignments.find((assignment) => assignment.id === this.selectedAssignmentId);
  },
  /** The groups the search finds. A group whose name matches keeps all its assignments. */
  get shownGroups() {
    const query = this.assignmentQuery;
    const { form } = this;
    return this.groups.flatMap((group) => {
      const assignments = matches(group.name.toLowerCase(), query)
        ? group.assignments
        : group.assignments.filter((assignment) => matches(assignment.name.toLowerCase(), query));
      if (query && !assignments.length) return [];
      return {
        ...group,
        weightLabel: this.appliesGroupWeights ? `${group.weight}%` : "",
        countLabel: counted(group.assignments.length, "assignment"),
        editing: form.kind === "group" && form.id === group.id,
        confirmingDelete: this.groupToDelete.id === group.id,
        addingAssignment: form.kind === "assignment" && !form.id && form.groupId === group.id,
        otherGroups: this.groups.filter((other) => other.id !== group.id),
        assignments: assignments.map((assignment) => ({
          ...assignment,
          editing: form.kind === "assignment" && form.id === assignment.id,
          marksLabel: marksLabel(assignment.pointsPossible),
        })),
      };
    });
  },

  /** The course's groups and their assignments; call inside a `run`. */
  async loadGroups() {
    const [course, groups] = await Promise.all([
      this.client.course(this.courseId),
      this.client.assignmentGroups(this.courseId),
    ]);
    this.appliesGroupWeights = course.apply_assignment_group_weights;
    this.groups = groups.map(describeGroup);
  },
  groupById(id) {
    return this.groups.find((group) => group.id === id);
  },
  /** Removes the assignment `id` from its group, and returns it. */
  removeAssignment(id) {
    for (const group of this.groups) {
      const index = group.assignments.findIndex((assignment) => assignment.id === id);
      if (index >= 0) return group.assignments.splice(index, 1)[0];
    }
  },

  openAssignmentPicker() {
    Object.assign(this, { assignmentQuery: "", selectedAssignmentId: this.assignmentId });
    this.pickerStatus.error = "";
    this.closeForms();
    openDialog(document.getElementById("assignment-picker"));
  },
  useSelectedAssignment() {
    this.assignmentId = this.selectedAssignmentId;
    document.getElementById("assignment-picker").close();
    this.choseAssignment();
  },

  newAssignment(group) {
    this.form = { ...EMPTY_FORM, kind: "assignment", groupId: group.id, name: this.config.name, points: this.maxMarks };
  },
  editAssignment(assignment) {
    this.form = {
      ...EMPTY_FORM,
      kind: "assignment",
      id: assignment.id,
      name: assignment.name,
      points: assignment.pointsPossible,
    };
  },
  newGroup() {
    this.form = { ...EMPTY_FORM, kind: "group", weight: 0 };
  },
  editGroup(group) {
    this.form = { ...EMPTY_FORM, kind: "group", id: group.id, name: group.name, weight: group.weight };
  },
  askToDeleteGroup(group) {
    this.closeForms();
    this.groupToDelete = { id: group.id, moveAssignmentsTo: this.groups.find((other) => other.id !== group.id).id };
  },
  closeForms() {
    this.form = { ...EMPTY_FORM };
    this.groupToDelete = { id: "", moveAssignmentsTo: "" };
  },
  /** Focuses the form's first field, with the form in view. */
  focusForm(field) {
    field.focus({ preventScroll: true });
    field.closest("li, section").scrollIntoView({ block: "nearest" });
  },

  /** Saves the open form in Canvas. */
  async saveForm() {
    const { kind, id, groupId, points, weight } = this.form;
    const name = this.form.name.trim();
    const saved = await this.run(
      "Saving in Canvas…",
      async () => {
        if (kind === "group") {
          const group = { name, ...(this.appliesGroupWeights && { group_weight: weight }) };
          if (id) {
            const updated = await this.client.updateAssignmentGroup(this.courseId, id, group);
            Object.assign(this.groupById(id), { name: updated.name, weight: updated.group_weight });
          } else {
            this.groups.push(describeGroup(await this.client.createAssignmentGroup(this.courseId, group)));
          }
          return true;
        }
        const assignment = { name, points_possible: points };
        if (id) {
          const updated = await this.client.updateAssignment(this.courseId, id, assignment);
          Object.assign(
            this.assignments.find((candidate) => candidate.id === id),
            describeAssignment(updated),
          );
        } else {
          const created = describeAssignment(
            await this.client.createAssignment(this.courseId, { ...assignment, assignment_group_id: groupId }),
          );
          this.groupById(groupId).assignments.push(created);
          this.selectedAssignmentId = created.id;
        }
        return true;
      },
      this.pickerStatus,
    );
    if (saved) this.closeForms();
  },

  async moveAssignment(assignment, group) {
    await this.run(
      "Moving in Canvas…",
      async () => {
        await this.client.updateAssignment(this.courseId, assignment.id, { assignment_group_id: group.id });
        this.groupById(group.id).assignments.push(this.removeAssignment(assignment.id));
      },
      this.pickerStatus,
    );
  },
  async deleteAssignment(assignment) {
    const deleted = await this.run(
      "Deleting in Canvas…",
      async () => {
        await this.client.deleteAssignment(this.courseId, assignment.id);
        this.removeAssignment(assignment.id);
        return true;
      },
      this.pickerStatus,
    );
    if (deleted) this.clearChosenAssignmentIfDeleted();
  },
  async deleteGroup() {
    const group = this.groupById(this.groupToDelete.id);
    const target = group.assignments.length ? this.groupById(this.groupToDelete.moveAssignmentsTo) : null;
    const deleted = await this.run(
      "Deleting in Canvas…",
      async () => {
        await this.client.deleteAssignmentGroup(this.courseId, group.id, target?.id);
        target?.assignments.push(...group.assignments);
        this.groups.splice(this.groups.indexOf(group), 1);
        return true;
      },
      this.pickerStatus,
    );
    if (deleted) {
      this.closeForms();
      this.clearChosenAssignmentIfDeleted();
    }
  },
  /** Clears the saved choice once its assignment is deleted. */
  clearChosenAssignmentIfDeleted() {
    if (this.assignmentId && !this.chosenAssignment) {
      this.assignmentId = "";
      this.choseAssignment();
    }
  },
});
