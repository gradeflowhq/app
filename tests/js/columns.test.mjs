import assert from "node:assert/strict";
import { test } from "node:test";

import {
  detectColumns,
  detectHeaderRow,
  detectStudentRows,
  isAnswerKeyRow,
} from "../../src/gradeflow_app/static/gradeflow/lib/columns.js";

test("ExamSoft file: StudentID, Qn answers with their Qn Pts", () => {
  const header = [
    "StudentID",
    "Last Name",
    "First Name",
    "Email",
    "Points",
    "Raw",
    "Q1",
    "Q1 Pts",
    "Q2",
    "Q2 Pts",
    "Q10",
    "Q10 Pts",
  ];
  const rows = [
    ["S1000001", "Tan", "Ann", "e1@example.edu", "7", "7", "A", "1", "B,C", "2", "x = 3", "4"],
    ["S1000002", "Lim", "Ben", "e2@example.edu", "5", "5", "B", "0", "C", "1", "x = 4", "4"],
  ];
  assert.deepEqual(detectColumns(header, rows), {
    studentId: "StudentID",
    answers: ["Q1", "Q2", "Q10"],
    marks: { Q1: "Q1 Pts", Q2: "Q2 Pts", Q10: "Q10 Pts" },
  });
});

test("generic CSV with Question n headers and assorted marks suffixes", () => {
  const header = [
    "Matric No",
    "Name",
    "Question 1",
    "Question 1 Marks",
    "Question 2",
    "question 2 score",
    "Question 3",
    "Total Score",
  ];
  const rows = [
    ["S1000001", "Ann Tan", "Paris", "1", "42", "2", "True", "3"],
    ["S1000002", "Ben Lim", "Rome", "0", "41", "0", "False", "0"],
  ];
  assert.deepEqual(detectColumns(header, rows), {
    studentId: "Matric No",
    answers: ["Question 1", "Question 2", "Question 3"],
    marks: { "Question 1": "Question 1 Marks", "Question 2": "question 2 score" },
  });
});

test("numbered, lettered and core-style columns", () => {
  const header = ["Username", "1", "1 points", "2a", "2b", "Part A", "Part B", "Q3", "Q3__points"];
  const { answers, marks } = detectColumns(header, [["u1", "A", "1", "B", "C", "yes", "no", "D", "1"]]);
  assert.deepEqual(answers, ["1", "2a", "2b", "Part A", "Part B", "Q3"]);
  assert.deepEqual(marks, { 1: "1 points", Q3: "Q3__points" });
});

test("an Email column identifies students when nothing better exists", () => {
  const header = ["Timestamp", "Email Address", "Full Name", "Q1", "Q2"];
  const rows = [
    ["2026-10-01 10:00:00", "ann@example.edu", "Ann Tan", "A", "B"],
    ["2026-10-01 10:01:00", "ben@example.edu", "Ben Lim", "C", "D"],
  ];
  assert.equal(detectColumns(header, rows).studentId, "Email Address");
});

test("ranks student IDs, then logins, then email, then any other ID; values must be unique", () => {
  const header = ["ID", "Email", "Login ID", "Student Number", "Q1"];
  const studentIdColumn = (rows) => detectColumns(header, rows).studentId;
  assert.equal(
    studentIdColumn([
      ["1", "a@x.edu", "e1", "A1", "A"],
      ["2", "b@x.edu", "e2", "A2", "B"],
    ]),
    "Student Number",
  );
  assert.equal(
    studentIdColumn([
      ["1", "a@x.edu", "e1", "A1", "A"],
      ["2", "b@x.edu", "e2", "A1", "B"],
    ]),
    "Login ID",
  );
  assert.equal(
    studentIdColumn([
      ["1", "a@x.edu", "e1", "A1", "A"],
      ["2", "b@x.edu", "e1", "A1", "B"],
    ]),
    "Email",
  );
  assert.equal(
    studentIdColumn([
      ["1", "a@x.edu", "e1", "A1", "A"],
      ["2", "a@x.edu", "e1", "A1", "B"],
    ]),
    "ID",
  );
  assert.equal(
    studentIdColumn([
      ["1", "a@x.edu", "e1", "A1", "A"],
      ["1", "a@x.edu", "e1", "A1", "B"],
    ]),
    null,
  );
});

test("falls back to a column of unique ID-like values, or none", () => {
  const header = ["Candidate", "Surname", "Q1"];
  const rows = [
    ["X1234567", "Tan", "A"],
    ["X7654321", "Lim", "B"],
  ];
  assert.equal(detectColumns(header, rows).studentId, "Candidate");
  assert.equal(
    detectColumns(
      ["Surname", "Q1"],
      [
        ["Tan", "A"],
        ["Lim", "B"],
      ],
    ).studentId,
    null,
  );
});

test("without question-like headers, the remaining non-summary columns are answers", () => {
  const header = ["Timestamp", "Email", "Name", "What is 6 x 7?", "Explain recursion.", "Score", "Grade"];
  const rows = [["2026-10-01 10:00", "ann@example.edu", "Ann Tan", "42", "It calls itself.", "2", "A"]];
  assert.deepEqual(detectColumns(header, rows), {
    studentId: "Email",
    answers: ["What is 6 x 7?", "Explain recursion."],
    marks: {},
  });
});

test("detectHeaderRow skips title lines above the header", () => {
  assert.equal(
    detectHeaderRow([
      ["StudentID", "Q1"],
      ["A1", "B"],
    ]),
    0,
  );
  assert.equal(
    detectHeaderRow([
      ["Biology Midterm", "", "", ""],
      ["Exported 2026-10-01", "", "", ""],
      [],
      ["StudentID", "Last Name", "Q1", "Q1 Pts"],
      ["S1000001", "Tan", "A", "1"],
    ]),
    3,
  );
  assert.equal(
    detectHeaderRow([
      ["1", "2", "3"],
      ["4", "5", "6"],
    ]),
    0,
  );
  assert.equal(detectHeaderRow([]), 0);
});

test("detectStudentRows leaves out leading and trailing rows without an ID", () => {
  const rows = [
    ["StudentID", "Q1"],
    ["Points possible", "1"],
    ["S1000001", "A"],
    [],
    ["S1000002", "B"],
    ["", "C"],
    ["S1000003", "B"],
    ["", ""],
    ["Average", "0.5"],
    ["Total", "2"],
    [""],
  ];
  assert.deepEqual(detectStudentRows(rows, 1, 0), [2, 6]);
  assert.deepEqual(detectStudentRows(rows, 3, 0), [4, 6]);
});

test("detectStudentRows keeps any row whose ID looks like one, whatever its format", () => {
  const emailRows = (...values) => [["Email"], ...values.map((value) => [value])];
  assert.deepEqual(detectStudentRows(emailRows("e1@u.edu", "BEN2@U.EDU", "Mean"), 1, 0), [1, 2]);
  assert.deepEqual(detectStudentRows(emailRows("12345", "678", "Total: 2"), 1, 0), [1, 2]);
  assert.deepEqual(
    detectStudentRows(emailRows("V1234567", "S1000001", "S1000002", "S1000003", "X99"), 1, 0),
    [1, 5],
    "exchange students first or last",
  );
  assert.deepEqual(
    detectStudentRows(emailRows("alice", "S1000001", "zoe.tan", "Max", "Std Dev"), 1, 0),
    [1, 3],
    "usernames without digits",
  );
});

test("detectStudentRows keeps every row below the header without IDs to compare", () => {
  assert.deepEqual(detectStudentRows([["Name"], ["Ann"], ["Ben"]], 1, -1), [1, 2]);
  assert.deepEqual(detectStudentRows([["StudentID"], ["", "x"]], 1, 0), [1, 1]);
  assert.deepEqual(detectStudentRows([["StudentID"]], 1, 0), [1, 0]);
});

test("ExamSoft file with its answer key row: neither it nor the row below is a student", () => {
  const rows = [
    [
      "StudentID",
      "First Name",
      "Last Name",
      "Points",
      "PercentageScore",
      "Raw",
      "Q1",
      "Q1 Pts",
      "Q2",
      "Q2 Pts",
      "Assessment Name",
    ],
    ["ANSWER KEY -->", "", "", "", "", "", "A,C", "", "0.63|0.625|~Terminal|terminal|", "", "Midterm"],
    ["Exam Takers -->", "", "", "", "", "", "A", "", "1~2", "", "Midterm"],
    ["S1000001", "Ann", "Tan", "3", "100", "3", "A,C", "1", "0.63~Terminal", "2", "Midterm"],
    ["S1000002", "Ben", "Lim", "1", "33", "1", "B", "0", "0.6~Cutoff", "1", "Midterm"],
  ];
  const header = rows[detectHeaderRow(rows)];
  assert.deepEqual(detectColumns(header, rows.slice(1)), {
    studentId: "StudentID",
    answers: ["Q1", "Q2"],
    marks: { Q1: "Q1 Pts", Q2: "Q2 Pts" },
  });
  assert.deepEqual(detectStudentRows(rows, 1, 0), [3, 4]);
  assert.deepEqual(
    rows.map((row) => isAnswerKeyRow(row, 0)),
    [false, true, false, false, false],
  );
});

test("isAnswerKeyRow ignores case and surrounding spaces", () => {
  assert.ok(isAnswerKeyRow(["  answer key -->  "], 0));
  assert.ok(isAnswerKeyRow(["x", "Answer Key-->"], 1));
  assert.ok(!isAnswerKeyRow(["ANSWER KEY -->"], -1));
  assert.ok(!isAnswerKeyRow(["Key"], 0));
});
