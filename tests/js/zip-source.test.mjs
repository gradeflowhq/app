import assert from "node:assert/strict";
import { test } from "node:test";

import { zipSource } from "../../src/gradeflow_app/static/gradeflow/components/import/zip-source.js";

const STUDENTS = ["atan", "blim", "cong", "dkoh"];

const zip = (extraPaths) => ({
  texts: new Map(
    [
      ...STUDENTS.flatMap((student) => ["solution.py", "notes.md"].map((name) => `lab1/${student}/${name}`)),
      ...extraPaths,
    ].map((path) => [path, "text"]),
  ),
  skipped: {},
});

const names = (rows) => rows.map((row) => row.name);

test("files the saved settings leave out are listed once and don't need a question", () => {
  const saved = { file_pattern: "", file_questions: { "solution.py": "solution", "notes.md": "", "draft.py": "" } };
  const source = zipSource(zip(["lab1/atan/draft.py", "lab1/blim/extra.py"]), saved, ["solution"]);
  assert.deepEqual(names(source.commonFileRows), ["notes.md", "solution.py"]);
  assert.deepEqual(names(source.noQuestionRows), ["extra.py"]);
  assert.deepEqual(names(source.otherFileRows), ["draft.py"]);
  assert.equal(source.extrasSummary, "1 file with no question");
  assert.equal(source.needsReview, true);
});
