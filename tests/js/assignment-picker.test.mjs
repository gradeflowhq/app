import assert from "node:assert/strict";
import { test } from "node:test";

import { assignmentPickerMixin } from "../../src/gradeflow_app/static/gradeflow/components/canvas/assignment-picker.js";

test("the search finds a group by name, with or without assignments", () => {
  const picker = assignmentPickerMixin();
  picker.groups = [
    { id: "1", name: "Labs", weight: 0, assignments: [{ id: "10", name: "Lab 1" }] },
    { id: "2", name: "Lab extras", weight: 0, assignments: [] },
    { id: "3", name: "Exams", weight: 0, assignments: [{ id: "30", name: "Final" }] },
  ];
  picker.assignmentQuery = "lab";
  assert.deepEqual(
    picker.shownGroups.map((group) => group.name),
    ["Labs", "Lab extras"],
  );
});
