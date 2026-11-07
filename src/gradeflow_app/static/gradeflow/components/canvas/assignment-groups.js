// Choosing a Canvas course's assignment group, or naming a new one, which is made in Canvas at once. In a course that
// weights its groups, a new one gets a share of the final grade: what the others leave, to start.

import { showToast } from "../../services/toasts.js";
import { listPicker, pickerItem } from "./list-picker.js";

const describeGroup = (group, weighted) =>
  Object.assign(pickerItem(group.id, group.name, weighted ? `Worth ${group.group_weight}%` : ""), {
    weight: group.group_weight,
  });

/** The mixin that chooses a group in the course `courseId`. */
export const assignmentGroupsMixin = () => ({
  group: listPicker("group"),
  weighted: false,
  newGroupName: "GradeFlow",
  newGroupWeight: 0,

  get canCreateGroup() {
    const weight = this.newGroupWeight;
    return Boolean(
      this.newGroupName.trim() && !this.isBusy && (!this.weighted || (Number.isFinite(weight) && weight >= 0)),
    );
  },
  /** Call inside a `run`: a second `run` right after would hide and show the busy state in one frame, which x-show
   * misses. */
  async loadGroups(preferred) {
    const [course, groups] = await Promise.all([
      this.client.course(this.courseId),
      this.client.assignmentGroups(this.courseId),
    ]);
    this.weighted = course.apply_assignment_group_weights;
    this.group.items = groups.map((group) => describeGroup(group, this.weighted));
    const has = (id) => this.group.items.some((group) => group.id === id);
    if (!has(this.group.id)) this.group.id = has(preferred) ? preferred : (this.group.items[0]?.id ?? "");
  },
  newGroup() {
    const assignedWeight = this.group.items.reduce((total, group) => total + group.weight, 0);
    this.group.naming = true;
    this.newGroupWeight = Math.max(0, Number((100 - assignedWeight).toFixed(10)));
  },
  async createGroup() {
    if (!this.canCreateGroup) return;
    const group = { name: this.newGroupName.trim(), ...(this.weighted && { group_weight: this.newGroupWeight }) };
    const created = await this.run("Creating the assignment group…", () =>
      this.client.createAssignmentGroup(this.courseId, group),
    );
    if (!created) return;
    this.group.items.push(describeGroup(created, this.weighted));
    this.group.id = String(created.id);
    this.group.naming = false;
    this.group.close();
    showToast(`Created the assignment group “${created.name}” in Canvas.`, "success");
  },
});
