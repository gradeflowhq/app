/**
 * Detects the roles of columns in an exam results file: the student ID column,
 * answer columns and the marks column paired with each answer column.
 */

/** Q1, Question 2, Item 3b, Problem 4.1, Part A, 1, 2a. */
const QUESTION = /^(?:(?:q|question|item|problem|ans|answer)[\s#._-]*\d|part[\s#._-]+[a-z\d]|\d)[\w.-]*$/i;

/** Tested against lowercased alphanumerics, best first. */
const ID_HEADERS = [
  /^(?:student(?:id|no|num|number)|matric[a-z]*)$/,
  /^(?:sis(?:user)?id|login(?:id)?|user(?:name|id)|netid)$/,
  /^email(?:address)?$/,
  /id$/,
];

/** Names, contact details, timings and summary scores; tested like ID_HEADERS. */
const NON_ANSWER_HEADER = /^(?:(?:first|last|full|given|preferred|sur)?name|email.*|timestamp|date|time|duration|section|group|status|rank|percentile|total.*|(?:raw|final|overall)?(?:score|points|pts|marks?|grade|percent(?:age)?))$/;

const MARKS_SUFFIX = '[\\s_-]*(?:pts|points?|marks?|score)$';

const normalize = (header) => header.toLowerCase().replace(/[^a-z\d]/g, '');
const columnValues = (rows, index) => rows.map((row) => (row[index] ?? '').trim());

const unique = (values) => {
  const present = values.filter(Boolean);
  return new Set(present).size === present.length;
};

/** Whether a value could be a student ID, of any format: "S1234567", "u1234567", "12345", "e1@u.edu"; not
 * "Average" or "Total: 2". */
const couldBeId = (value) => /^[\w.@-]+$/.test(value) && /\d/.test(value);

const looksLikeIds = (values) => values.length > 0 && unique(values) && values.every((v) => couldBeId(v) && /[a-z@]/i.test(v));

/** Summary rows' labels that are written like an ID. */
const SUMMARY_ROW = /^(?:average|mean|median|mode|total|sum|count|max(?:imum)?|min(?:imum)?|std(?:dev)?|sd|variance|key)$/i;

/** Whether a row's ID cell could be a student's, usernames such as "alice" included; not "Average", "Total: 2" or
 * "Points possible". */
const couldBeStudentId = (value) => /^[\w.@-]+$/.test(value) && !SUMMARY_ROW.test(value);

/** ExamSoft's "Answer Key in First Row" puts this in the ID column. */
const ANSWER_KEY_ROW = /^answer key\s*-->$/i;

/** Whether `row` is a results file's answer key, which is never a student. */
export const isAnswerKeyRow = (row, idIndex) => ANSWER_KEY_ROW.test((row[idIndex] ?? '').trim());

/** @returns {number} the first row with values in most of the columns, usually 0 */
export const detectHeaderRow = (rows) => {
  const filledCounts = rows.slice(0, 20).map((row) => row.filter((cell) => cell.trim()).length);
  const widestCount = Math.max(0, ...filledCounts);
  return Math.max(0, filledCounts.findIndex((count) => count > widestCount / 2));
};

const detectStudentId = (header, rows, questions) => {
  const values = header.map((_, i) => columnValues(rows, i));
  const otherColumns = [...header.keys()].filter((i) => !questions.has(header[i]));
  for (const pattern of ID_HEADERS) {
    const i = otherColumns.find((i) => pattern.test(normalize(header[i])) && unique(values[i]));
    if (i !== undefined) return header[i];
  }
  const i = otherColumns.find((i) => !NON_ANSWER_HEADER.test(normalize(header[i])) && looksLikeIds(values[i]));
  return i === undefined ? null : header[i];
};

/**
 * @param {string[]} header
 * @param {string[][]} rows the data rows below the header
 * @returns {{studentId: string | null, answers: string[], marks: Record<string, string>}}
 */
export const detectColumns = (header, rows) => {
  const questions = new Set(header.filter((h) => QUESTION.test(h.trim())));
  const studentId = detectStudentId(header, rows, questions);
  const candidates = questions.size
    ? [...questions]
    : header.filter((h) => h.trim() && h !== studentId && !NON_ANSWER_HEADER.test(normalize(h)));

  const marks = {};
  for (const answer of candidates) {
    const marksHeaderPattern = new RegExp(`^${RegExp.escape(answer.trim())}${MARKS_SUFFIX}`, 'i');
    const column = header.find((h) => h !== answer && marksHeaderPattern.test(h.trim()));
    if (column) marks[answer] = column;
  }
  const markColumns = new Set(Object.values(marks));
  return { studentId, answers: candidates.filter((h) => !markColumns.has(h)), marks };
};

/**
 * The first and last student rows from `startRow` on: leading and trailing rows without an ID, such as "Points
 * possible" or "Average" rows, are left out; students with an unusual ID format are not.
 * @param {string[][]} rows
 * @param {number} idIndex the student ID column, or -1
 * @returns {[number, number]} indices into `rows`, inclusive
 */
export const detectStudentRows = (rows, startRow, idIndex) => {
  const ids = rows.map((row) => couldBeStudentId((row[idIndex] ?? '').trim()));
  const first = ids.indexOf(true, startRow);
  if (first === -1) return [startRow, rows.length - 1];
  return [first, ids.lastIndexOf(true)];
};
