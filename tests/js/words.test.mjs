import assert from "node:assert/strict";
import { test } from "node:test";

import { counted, formatList, matches } from "../../src/gradeflow_app/static/gradeflow/lib/words.js";

test("counts in the singular only for one", () => {
  assert.equal(counted(1, "student"), "1 student");
  assert.equal(counted(0, "student"), "0 students");
  assert.equal(counted(2.5, "mark"), "2.5 marks");
  assert.equal(counted(6452, "file"), "6,452 files");
});

test("takes an irregular plural", () => {
  assert.equal(counted(1, "student's", "students'"), "1 student's");
  assert.equal(counted(3, "student's", "students'"), "3 students'");
});

test("matches text that has every word typed, in any case and order", () => {
  assert.ok(matches("introductory biology", "  Biology intro "));
  assert.ok(matches("introductory biology", ""));
  assert.ok(!matches("introductory biology", "biology chemistry"));
});

test("a list reads as a sentence", () => {
  assert.equal(formatList(["a"]), "a");
  assert.equal(formatList(["a", "b", "c"]), "a, b, and c");
});
