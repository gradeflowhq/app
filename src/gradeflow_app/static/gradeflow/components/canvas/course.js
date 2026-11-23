// A course's Canvas page: the Canvas course its marks go to, chosen in the dialog #course-picker.

import { counted, matches } from "../../lib/words.js";
import { openDialog } from "../../services/dialogs.js";
import { connectionMixin, mergeMixins, readConfig } from "./connection.js";

// A course with its code, term and size under its name. Newest first, by its term's start, else its own, as many
// terms have none.
const describeCourse = (course) => {
  const students = course.total_students;
  const detail = [course.course_code, course.term?.name, students != null && counted(students, "student")];
  return {
    id: String(course.id),
    name: course.name,
    detail: detail.filter(Boolean).join(" · "),
    started: course.term?.start_at ?? course.start_at ?? course.created_at ?? "",
  };
};
const newestFirst = (a, b) => b.started.localeCompare(a.started) || a.name.localeCompare(b.name);

export const canvasCourse = () =>
  mergeMixins(connectionMixin(), {
    courseId: "",
    courses: [],
    courseQuery: "",

    /** The chosen course; the saved one's name shows while the list loads. */
    get chosenCourse() {
      const { course: savedId, courseName: savedName } = this.config;
      const savedCourse =
        this.courseId && this.courseId === savedId ? { id: savedId, name: savedName, detail: "" } : null;
      return this.courses.find(({ id }) => id === this.courseId) ?? savedCourse;
    },
    get shownCourses() {
      return this.courses.filter(({ name, detail }) => matches(`${name} ${detail}`.toLowerCase(), this.courseQuery));
    },

    async init() {
      this.config = readConfig(this.$el);
      this.courseId = this.config.course;
      await this.loadConnection();
      if (this.canvasConnection) this.load();
    },
    load() {
      return this.run("Loading courses…", async () => {
        this.courses = (await this.client.courses()).map(describeCourse).sort(newestFirst);
        return true;
      });
    },
    openCoursePicker() {
      this.courseQuery = "";
      const dialog = document.getElementById("course-picker");
      openDialog(dialog);
      dialog.querySelector("[aria-selected=true]")?.scrollIntoView({ block: "nearest" });
    },
    chooseCourse(course) {
      this.courseId = course.id;
      document.getElementById("course-picker").close();
    },
  });
