// The roles of a submissions file's columns: the student ID column, the answer columns and the marks column paired
// with each answer column.

import { cellText } from "./cells.js";

/** Q1, Question 2, Item 3b, Problem 4.1, Part A, 1, 2a. */
const QUESTION = /^(?:(?:q|question|item|problem|ans|answer)[\s#._-]*\d|part[\s#._-]+[a-z\d]|\d)[\w.-]*$/i;

/** Tested against a header's lowercase letters and digits, best first. */
const ID_HEADERS = [
  /^(?:student(?:id|no|num|number)|matric[a-z]*)$/,
  /^(?:sis(?:user)?id|login(?:id)?|user(?:name|id)|netid)$/,
  /^email(?:address)?$/,
  /id$/,
];

/** Names, contact details, timings and summary scores; tested like ID_HEADERS. */
const NON_ANSWER_HEADER =
  /^(?:(?:first|last|full|given|preferred|sur)?name|email.*|timestamp|date|time|duration|section|group|status|rank|percentile|total.*|(?:raw|final|overall)?(?:score|points|pts|marks?|grade|percent(?:age)?))$/;

const MARKS_SUFFIX = "[\\s_-]*(?:pts|points?|marks?|score)$";

const headerKey = (header) => header.toLowerCase().replace(/[^a-z\d]/g, "");
const columnValues = (rows, index) => rows.map((row) => cellText(row[index]));

const hasNoDuplicates = (values) => {
  const present = values.filter(Boolean);
  return new Set(present).size === present.length;
};

/** Whether every value is a distinct ID with letters and digits, such as "S1234567", "u1234567" or "e1@u.edu"; not
 * "Average", "Total: 2" or a plain number. */
const looksLikeIds = (values) =>
  values.length > 0 &&
  hasNoDuplicates(values) &&
  values.every((value) => /^[\w.@-]+$/.test(value) && /\d/.test(value) && /[a-z@]/i.test(value));

/** Summary rows' labels that are written like an ID. */
const SUMMARY_ROW =
  /^(?:average|mean|median|mode|total|sum|count|max(?:imum)?|min(?:imum)?|std(?:dev)?|sd|variance|key)$/i;

/** Whether a row's ID cell could be a student's, usernames such as "alice" included; not "Average", "Total: 2" or
 * "Points possible". */
const couldBeStudentId = (value) => /^[\w.@-]+$/.test(value) && !SUMMARY_ROW.test(value);

/** ExamSoft's "Answer Key in First Row" puts this in the ID column. */
const ANSWER_KEY_ROW = /^answer key\s*-->$/i;

/** Whether `row` is a submissions file's answer key, which is never a student. */
export const isAnswerKeyRow = (row, idIndex) => ANSWER_KEY_ROW.test(cellText(row[idIndex]));

/** The first row with values in most of the columns, usually 0. */
export const detectHeaderRow = (rows) => {
  const filledCounts = rows.slice(0, 20).map((row) => row.filter(cellText).length);
  const widestCount = Math.max(0, ...filledCounts);
  return Math.max(
    0,
    filledCounts.findIndex((count) => count > widestCount / 2),
  );
};

const detectStudentId = (header, rows, questions) => {
  const values = header.map((_, i) => columnValues(rows, i));
  const otherColumns = [...header.keys()].filter((i) => !questions.has(header[i]));
  for (const pattern of ID_HEADERS) {
    const i = otherColumns.find((i) => pattern.test(headerKey(header[i])) && hasNoDuplicates(values[i]));
    if (i !== undefined) return header[i];
  }
  const i = otherColumns.find((i) => !NON_ANSWER_HEADER.test(headerKey(header[i])) && looksLikeIds(values[i]));
  return i === undefined ? null : header[i];
};

/** The student ID column (null if none), the answer columns, and each answer column's marks column, from the
 * header and the data rows below it. */
export const detectColumns = (header, rows) => {
  const questions = new Set(header.filter((name) => QUESTION.test(name.trim())));
  const studentId = detectStudentId(header, rows, questions);
  const candidates = questions.size
    ? [...questions]
    : header.filter((name) => name.trim() && name !== studentId && !NON_ANSWER_HEADER.test(headerKey(name)));

  const marks = {};
  for (const answer of candidates) {
    const marksHeader = new RegExp(`^${RegExp.escape(answer.trim())}${MARKS_SUFFIX}`, "i");
    const column = header.find((name) => name !== answer && marksHeader.test(name.trim()));
    if (column) marks[answer] = column;
  }
  const marksColumns = new Set(Object.values(marks));
  return { studentId, answers: candidates.filter((name) => !marksColumns.has(name)), marks };
};

/** The indices of the first and last student rows from `startRow` on, inclusive. Leading and trailing rows without
 * an ID, such as "Points possible" or "Average", are left out; students with an unusual ID format are not. `idIndex`
 * is -1 without an ID column. */
export const detectStudentRows = (rows, startRow, idIndex) => {
  const ids = rows.map((row) => couldBeStudentId(cellText(row[idIndex])));
  const first = ids.indexOf(true, startRow);
  if (first === -1) return [startRow, rows.length - 1];
  return [first, ids.lastIndexOf(true)];
};
