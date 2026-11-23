// Choosing the Canvas course's assignment in the dialog #assignment-picker (exports/assignment_picker.html): a card
// per assignment group with its assignments. Groups and assignments are created, edited and deleted in Canvas straight
// away. The component it's mixed into has `courseId`, `maxMarks`, `config.name` and `changeAssignment(id)`.

import { counted, matches } from "../../lib/words.js";
import { openDialog } from "../../services/dialogs.js";

const EMPTY_FORM = Object.freeze({
  kind: "",
  id: "",
  groupId: "",
  name: "",
  points: "",
  weight: "",
  moveAssignmentsTo: "",
});

const describeAssignment = (assignment) => ({
  id: String(assignment.id),
  name: assignment.name,
  pointsPossible: assignment.points_possible,
  gradingType: assignment.grading_type,
  published: assignment.published,
  hasGrades: assignment.graded_submissions_exist,
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
  pendingAssignmentId: "",
  /** The busy label and error of the picker's Canvas requests. */
  pickerRequestState: { busyLabel: "", error: "" },
  /** The open form, by `kind`: an "assignment" or a "group", new without an `id`, or "delete-group". A new assignment
   * goes in the group `groupId`. A deleted group's assignments move to the group `moveAssignmentsTo`, or without one
   * are deleted with it. */
  form: { ...EMPTY_FORM },

  get assignments() {
    return this.groups.flatMap((group) => group.assignments);
  },
  get chosenAssignment() {
    return this.assignments.find((assignment) => assignment.id === this.assignmentId);
  },
  get isPickerBusy() {
    return Boolean(this.pickerRequestState.busyLabel);
  },
  get pendingAssignment() {
    return this.assignments.find((assignment) => assignment.id === this.pendingAssignmentId);
  },
  /** The groups the search finds. A group whose name matches keeps all its assignments. */
  get shownGroups() {
    const query = this.assignmentQuery;
    const { form } = this;
    return this.groups.flatMap((group) => {
      const nameMatches = matches(group.name.toLowerCase(), query);
      const assignments = nameMatches
        ? group.assignments
        : group.assignments.filter((assignment) => matches(assignment.name.toLowerCase(), query));
      if (!nameMatches && !assignments.length) return [];
      return {
        ...group,
        weightLabel: this.appliesGroupWeights ? `${group.weight}%` : "",
        assignmentCountLabel: counted(group.assignments.length, "assignment"),
        isEditing: form.kind === "group" && form.id === group.id,
        isConfirmingDelete: form.kind === "delete-group" && form.id === group.id,
        isAddingAssignment: form.kind === "assignment" && !form.id && form.groupId === group.id,
        otherGroups: this.groups.filter((other) => other.id !== group.id),
        assignments: assignments.map((assignment) => ({
          ...assignment,
          isEditing: form.kind === "assignment" && form.id === assignment.id,
          marksLabel: marksLabel(assignment.pointsPossible),
        })),
      };
    });
  },

  /** The course's groups and their assignments, with `course`, the Canvas course's request; call inside a `run`. */
  async loadGroups(course) {
    const [{ apply_assignment_group_weights }, groups] = await Promise.all([
      course,
      this.client.assignmentGroups(this.courseId),
    ]);
    this.appliesGroupWeights = apply_assignment_group_weights;
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
    Object.assign(this, { assignmentQuery: "", pendingAssignmentId: this.assignmentId });
    this.pickerRequestState.error = "";
    this.closeForm();
    openDialog(document.getElementById("assignment-picker"));
  },
  usePendingAssignment() {
    document.getElementById("assignment-picker").close();
    this.changeAssignment(this.pendingAssignmentId);
  },

  openNewAssignmentForm(group) {
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
  openNewGroupForm() {
    this.form = { ...EMPTY_FORM, kind: "group", weight: 0 };
  },
  editGroup(group) {
    this.form = { ...EMPTY_FORM, kind: "group", id: group.id, name: group.name, weight: group.weight };
  },
  askToDeleteGroup(group) {
    this.form = { ...EMPTY_FORM, kind: "delete-group", id: group.id, moveAssignmentsTo: group.otherGroups[0].id };
  },
  closeForm() {
    this.form = { ...EMPTY_FORM };
  },
  /** Focuses the form's first field, with the form in view. */
  focusForm(field) {
    field.focus({ preventScroll: true });
    field.closest("li, section").scrollIntoView({ block: "nearest" });
  },

  async saveForm() {
    const { kind, id, groupId, points, weight } = this.form;
    const name = this.form.name.trim();
    await this.run(
      "Saving in Canvas…",
      async () => {
        if (kind === "group") {
          const group = { name, ...(this.appliesGroupWeights && { group_weight: weight }) };
          if (id) {
            const updatedGroup = await this.client.updateAssignmentGroup(this.courseId, id, group);
            Object.assign(this.groupById(id), { name: updatedGroup.name, weight: updatedGroup.group_weight });
          } else {
            this.groups.push(describeGroup(await this.client.createAssignmentGroup(this.courseId, group)));
          }
        } else if (id) {
          const updatedAssignment = await this.client.updateAssignment(this.courseId, id, {
            name,
            points_possible: points,
          });
          Object.assign(
            this.assignments.find((assignment) => assignment.id === id),
            describeAssignment(updatedAssignment),
          );
        } else {
          const createdAssignment = describeAssignment(
            await this.client.createAssignment(this.courseId, {
              name,
              points_possible: points,
              assignment_group_id: groupId,
            }),
          );
          this.groupById(groupId).assignments.push(createdAssignment);
          this.pendingAssignmentId = createdAssignment.id;
        }
        this.closeForm();
      },
      this.pickerRequestState,
    );
  },

  async moveAssignment(assignment, destinationGroup) {
    await this.run(
      "Moving in Canvas…",
      async () => {
        await this.client.updateAssignment(this.courseId, assignment.id, { assignment_group_id: destinationGroup.id });
        destinationGroup.assignments.push(this.removeAssignment(assignment.id));
      },
      this.pickerRequestState,
    );
  },
  async deleteAssignment(assignment) {
    await this.run(
      "Deleting in Canvas…",
      async () => {
        await this.client.deleteAssignment(this.courseId, assignment.id);
        this.removeAssignment(assignment.id);
        if (this.assignmentId && !this.chosenAssignment) this.changeAssignment("");
      },
      this.pickerRequestState,
    );
  },
  async deleteGroup() {
    const group = this.groupById(this.form.id);
    const destinationGroup = this.groupById(this.form.moveAssignmentsTo);
    await this.run(
      "Deleting in Canvas…",
      async () => {
        await this.client.deleteAssignmentGroup(this.courseId, group.id, destinationGroup?.id);
        destinationGroup?.assignments.push(...group.assignments);
        this.groups.splice(this.groups.indexOf(group), 1);
        this.closeForm();
        if (this.assignmentId && !this.chosenAssignment) this.changeAssignment("");
      },
      this.pickerRequestState,
    );
  },
});
