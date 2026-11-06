// A spreadsheet of submissions under review: its header row, student rows and column roles.

import { cellText, detectColumns, detectHeaderRow, detectStudentRows, isAnswerKeyRow } from "./columns.js";
import { loadScript } from "./vendor.js";

const ROLES = { id: "Student ID", answer: "Answer", marks: "Marks", unused: "Not used" };
const MAX_LEFT_OUT_ROWS_SHOWN = 3;
const ZIP_SIGNATURE = [0x50, 0x4b, 0x03, 0x04];

const isBlankRow = (row) => !row.some(cellText);

const parseCsv = async (text) => {
  await loadScript("papaparse-5.7.0.min.js");
  // Not "|", which ExamSoft's answer key row is full of: a short file would be split on it.
  return Papa.parse(text, { delimitersToGuess: [",", "\t", ";"] }).data;
};

/** A CSV file's rows, blank ones included. Throws for a file that isn't text. */
export async function readCsv(bytes) {
  const text = new TextDecoder().decode(bytes);
  if (text.includes("\0")) throw new Error("not text");
  return parseCsv(text);
}

/** A CSV or Excel file's rows by sheet ("" for CSV), blank rows included so row numbers match the spreadsheet. Throws
 * for a file that isn't what its name says. */
export async function readTable(name, bytes) {
  if (/\.(csv|txt)$/i.test(name)) return { sheets: [], rows: { "": await readCsv(bytes) } };
  if (/\.xls[xmb]$/i.test(name) && !ZIP_SIGNATURE.every((byte, index) => bytes[index] === byte)) throw new Error("not a workbook");
  await loadScript("xlsx-0.20.3/xlsx.full.min.js");
  const workbook = XLSX.read(bytes, { dense: true });
  const options = { header: 1, raw: false, defval: "", blankrows: true, range: 0 };
  const rows = Object.fromEntries(workbook.SheetNames.map((sheet) => [sheet, XLSX.utils.sheet_to_json(workbook.Sheets[sheet], options)]));
  return { sheets: workbook.SheetNames, rows };
}

/** Whether a table has no values at all. */
export const isEmptyTable = ({ rows }) => !Object.values(rows).some((sheetRows) => sheetRows.some((row) => !isBlankRow(row)));

/** A spreadsheet under review, opened with its saved settings if it's reopened. */
export function tableSource({ sheets, rows }, savedSettings = null) {
  const source = {
    isZip: false,
    sheets,
    sheet: sheets.includes(savedSettings?.sheet) ? savedSettings.sheet : (sheets[0] ?? ""),
    rowsBySheet: rows,
    headerRow: 0,
    firstRow: 0,
    lastRow: -1,
    header: [],
    rowsInRange: [],
    columns: [],

    get roles() {
      return Object.entries(ROLES);
    },
    get rows() {
      return this.rowsBySheet[this.sheet] ?? [];
    },
    get studentColumn() {
      return this.columns.find((column) => column.role === "id")?.name ?? "";
    },
    get idIndex() {
      return this.header.indexOf(this.studentColumn);
    },
    /** The rows imported as students: those with an ID, never the answer key. */
    get studentRows() {
      return this.rowsInRange.filter((row) => cellText(row[this.idIndex]) && !isAnswerKeyRow(row, this.idIndex));
    },
    /** Rows with values that aren't counted as students, such as "Average" rows, which people should see. */
    get leftOutRows() {
      return [...this.rows.slice(this.headerRow + 1, this.firstRow), ...this.rows.slice(this.lastRow + 1)].filter((row) => !isBlankRow(row));
    },
    /** The row ExamSoft adds with the answer key, if the file has one. */
    get answerKey() {
      return this.rows.slice(this.headerRow + 1).find((row) => isAnswerKeyRow(row, this.idIndex));
    },
    get headerPreview() {
      const names = this.header.filter(Boolean);
      return names.slice(0, 4).join(", ") + (names.length > 4 ? ", …" : "");
    },
    get rowsPreview() {
      if (!this.rowsInRange.length) return "No students";
      const range = `${this.rowLabel(this.rowsInRange[0])} to ${this.rowLabel(this.rowsInRange.at(-1))}`;
      const leftOutLabels = this.leftOutRows.map((row) => this.rowLabel(row));
      if (!leftOutLabels.length) return range;
      const more = leftOutLabels.length > MAX_LEFT_OUT_ROWS_SHOWN ? ` and ${leftOutLabels.length - MAX_LEFT_OUT_ROWS_SHOWN} more` : "";
      return `${range}. Not included: ${leftOutLabels.slice(0, MAX_LEFT_OUT_ROWS_SHOWN).join(", ")}${more}`;
    },
    /** A row as people would recognise it: its student ID, or else its first value. */
    rowLabel(row) {
      if (isAnswerKeyRow(row, this.idIndex)) return "answer key";
      return cellText(row[this.idIndex]) || row.map(cellText).find(Boolean) || "";
    },

    get importedQuestions() {
      return this.columns.filter((column) => column.role === "answer").map((column) => column.name);
    },
    get ids() {
      return this.studentRows.map((row) => cellText(row[this.idIndex]));
    },
    get problem() {
      if (!this.studentColumn) return "Choose the column with student IDs.";
      if (!this.ids.length) return "No student IDs in these rows. Check the rows and columns.";
      if (!this.importedQuestions.length) return "Choose at least one answer column.";
      return "";
    },
    get extrasSummary() {
      const marksColumnCount = this.columns.filter((column) => column.role === "marks").length;
      return marksColumnCount ? `${marksColumnCount} with marks from the file` : "No marks columns";
    },
    get needsReview() {
      return this.leftOutRows.length > 0;
    },
    /** The uploaded columns' header and each student's cells, the ID first. */
    answersTable() {
      const uploaded = this.columns.filter((column) => column.role !== "unused").map((column) => column.index);
      const ordered = [this.idIndex, ...uploaded.filter((index) => index !== this.idIndex)];
      return { header: ordered.map((index) => this.header[index]), rows: this.studentRows.map((row) => ordered.map((index) => String(row[index] ?? ""))) };
    },
    /** The settings chosen, as upload form fields. */
    formFields() {
      const marksColumns = this.columns.filter((column) => column.role === "marks" && column.marksFor).map((column) => [column.marksFor, column.name]);
      const answerKey = this.answerKey && Object.fromEntries(this.importedQuestions.map((name) => [name, String(this.answerKey[this.header.indexOf(name)] ?? "")]));
      return {
        marks_columns: JSON.stringify(Object.fromEntries(marksColumns)),
        ...(answerKey && { answer_key: JSON.stringify(answerKey) }),
        sheet: this.sheet,
        header_row: this.headerRow,
        first_row: this.firstRow,
        last_row: this.lastRow,
      };
    },

    restore({ header_row, first_row, last_row, student_id_column, answer_columns, marks_columns }) {
      if (header_row === null || !(header_row < first_row && first_row <= last_row && last_row < this.rows.length)) return false;
      this.headerRow = header_row;
      this.header = this.rows[header_row].map(cellText);
      this.setRows(first_row, last_row);
      this.assignRoles({ studentId: student_id_column, answers: answer_columns, marks: marks_columns });
      return true;
    },
    detect(headerRow = detectHeaderRow(this.rows)) {
      this.headerRow = headerRow;
      this.header = (this.rows[headerRow] ?? []).map(cellText);
      const below = this.rows.slice(headerRow + 1).filter((row) => !isBlankRow(row));
      const { studentId } = detectColumns(this.header, below);
      this.setRows(...detectStudentRows(this.rows, headerRow + 1, this.header.indexOf(studentId)));
      this.assignRoles(detectColumns(this.header, this.rowsInRange));
    },
    setRows(first, last) {
      Object.assign(this, { firstRow: first, lastRow: last });
      this.rowsInRange = this.rows.slice(first, last + 1).filter((row) => !isBlankRow(row));
      for (const column of this.columns) column.sampleValues = this.sampleValuesOf(column.index);
    },
    /** Gives each column its role: `marks` maps answer columns to their marks columns. */
    assignRoles({ studentId, answers, marks }) {
      const marksFor = new Map(Object.entries(marks).map(([answer, column]) => [column, answer]));
      this.columns = this.header.map((name, index) => ({
        index,
        name,
        role: name === studentId ? "id" : answers.includes(name) ? "answer" : marksFor.has(name) ? "marks" : "unused",
        marksFor: marksFor.get(name) ?? "",
        sampleValues: this.sampleValuesOf(index),
      }));
    },
    sampleValuesOf(index) {
      return this.rowsInRange.slice(0, 3).map((row) => cellText(row[index])).join(", ");
    },

    pickSheet(event) {
      this.sheet = event.target.value;
      this.detect();
    },
    /** The 0-based row an input's row number names, kept within [min, max] and shown corrected. */
    rowNumber(event, min, max) {
      const typed = Number.parseInt(event.target.value, 10) - 1;
      const row = Number.isNaN(typed) ? min : Math.min(Math.max(typed, min), max);
      event.target.value = row + 1;
      return row;
    },
    setHeaderRow(event) {
      this.detect(this.rowNumber(event, 0, this.rows.length - 1));
    },
    setFirstRow(event) {
      this.setRows(this.rowNumber(event, this.headerRow + 1, this.lastRow), this.lastRow);
    },
    setLastRow(event) {
      this.setRows(this.firstRow, this.rowNumber(event, this.firstRow, this.rows.length - 1));
    },
    setRole(event) {
      const column = this.columns[Number(event.target.dataset.index)];
      column.role = event.target.value;
      if (column.role === "id") for (const other of this.columns) if (other !== column && other.role === "id") other.role = "unused";
      if (column.role === "marks" && !column.marksFor) column.marksFor = this.importedQuestions[0] ?? "";
    },
    setMarksFor(event) {
      this.columns[Number(event.target.dataset.index)].marksFor = event.target.value;
    },
  };
  if (!savedSettings || !source.restore(savedSettings)) source.detect();
  return source;
}
