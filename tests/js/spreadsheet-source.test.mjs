import assert from "node:assert/strict";
import { test } from "node:test";

import { spreadsheetSource } from "../../src/gradeflow_app/static/gradeflow/components/import/spreadsheet-source.js";

const SAVED = {
  sheet: "",
  header_row: 0,
  first_row: 1,
  last_row: 2,
  student_id_column: "Alt ID",
  answer_columns: ["Q1", "Q2"],
  marks_columns: { Q1: "Q1 Marks" },
};

const replacement = (rows) => spreadsheetSource({ sheets: [], rows: { "": rows } }, SAVED, { isReplacement: true });

const roles = (source) =>
  Object.fromEntries(source.columns.map((column) => [column.name, `${column.role} ${column.marksFor}`.trim()]));

test("a replacement keeps the saved columns and leaves the others unused", () => {
  const source = replacement([
    ["Student Number", "Q3", "Q1", "Alt ID", "Q2 Marks", "Q2", "Q1 Marks", "Q3 Marks", "Notes"],
    ["51000000", "c", "a", "A7000", "1", "b", "0", "1", "late"],
    ["51000037", "c", "a", "A7001", "0", "b", "1", "0", "ok"],
  ]);
  assert.deepEqual(roles(source), {
    "Student Number": "unused",
    Q3: "unused",
    Q1: "answer",
    "Alt ID": "id",
    "Q2 Marks": "unused",
    Q2: "answer",
    "Q1 Marks": "marks Q1",
    "Q3 Marks": "unused",
    Notes: "unused",
  });
});

test("a replacement without the saved columns is detected afresh", () => {
  const source = replacement([
    ["Student Number", "Q1", "Q2"],
    ["51000000", "a", "b"],
    ["51000037", "a", "b"],
  ]);
  assert.deepEqual(roles(source), { "Student Number": "id", Q1: "answer", Q2: "answer" });
});
