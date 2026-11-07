// An assessment's Canvas page: the Canvas course marks go to, and the group new assignments go in.

import { counted } from "../../lib/words.js";
import { assignmentGroupsMixin } from "./assignment-groups.js";
import { connectionMixin, mergeMixins, readConfig } from "./connection.js";
import { listPicker, pickerItem } from "./list-picker.js";

// A course with its code, term and size under its name. Newest first, by its term's start, else its own, as many
// terms have none.
const describeCourse = (course) => {
  const students = course.total_students;
  const detail = [course.course_code, course.term?.name, students != null && counted(students, "student")];
  return {
    ...pickerItem(course.id, course.name, detail.filter(Boolean).join(" · ")),
    started: course.term?.start_at ?? course.start_at ?? course.created_at ?? "",
  };
};
const newestFirst = (a, b) => b.started.localeCompare(a.started) || a.name.localeCompare(b.name);

export const canvasCourse = () =>
  mergeMixins(connectionMixin(), assignmentGroupsMixin(), {
    course: listPicker("course"),

    get courseId() {
      return this.course.id;
    },

    async init() {
      this.config = readConfig(this.$el);
      const { course, courseName, assignmentGroup, assignmentGroupName } = this.config;
      this.course.id = course;
      this.group.id = assignmentGroup;
      // The saved choices show while their lists load.
      this.course.placeholder = () => (course && this.course.id === course ? pickerItem(course, courseName) : null);
      this.group.placeholder = () =>
        assignmentGroup && this.group.id === assignmentGroup ? pickerItem(assignmentGroup, assignmentGroupName) : null;
      this.course.onChoose = () => {
        this.group.id = "";
        this.run("Loading assignment groups…", () => this.loadGroups());
      };
      await this.loadConnection();
      if (this.canvasConnection) this.load();
    },
    load() {
      return this.run("Loading courses…", async () => {
        this.course.items = (await this.client.courses()).map(describeCourse).sort(newestFirst);
        if (this.course.id) await this.loadGroups(this.config.assignmentGroup);
        return true;
      });
    },
  });
