// Importing a results file: read in the browser, rows and columns detected and checked, student IDs encrypted, and
// only the ID, answer and marks columns uploaded in the clear. The whole file is uploaded encrypted, so its columns
// can be changed later; names and every other column stay unreadable to the server.

import { detectColumns, detectHeaderRow, detectStudentRows, isAnswerKeyRow } from "./columns.js";
import { decryptFile, encryptFile, encryptId } from "./crypto.js";
import { dropZone } from "./drop.js";
import { script } from "./vendor.js";
import { modelHeaders } from "./models.js";
import { opens, passphraseFor, remember, remembered, stored } from "./passphrase.js";
import { plural } from "./words.js";

const ROLES = { id: "Student ID", answer: "Answer", marks: "Marks", ignore: "Not used" };
const MIN_PASSPHRASE = 8;
const LEFT_OUT_SHOWN = 3;

const text = (cell) => String(cell ?? "").trim();
const blank = (row) => !row.some(text);

const ZIP = [0x50, 0x4b, 0x03, 0x04];

/** Rows by sheet name ("" for CSV), blank rows included so row numbers match the spreadsheet. Throws for a file that
 * isn't what its name says: text with NUL bytes, or a newer Excel file that isn't a zip, which SheetJS would read as
 * text. */
async function readRows(name, bytes) {
  if (/\.(csv|txt)$/i.test(name)) {
    const contents = new TextDecoder().decode(bytes);
    if (contents.includes("\0")) throw new Error("not text");
    await script("papaparse-5.7.0.min.js");
    // Not "|", which ExamSoft's answer key row is full of: a short file would be split on it.
    const { data } = Papa.parse(contents, { delimitersToGuess: [",", "\t", ";"] });
    return { sheets: [], rows: { "": data } };
  }
  if (/\.xls[xmb]$/i.test(name) && !ZIP.every((byte, index) => bytes[index] === byte)) throw new Error("not a workbook");
  await script("xlsx-0.20.3/xlsx.full.min.js");
  const book = XLSX.read(bytes, { dense: true });
  const options = { header: 1, raw: false, defval: "", blankrows: true, range: 0 };
  const rows = Object.fromEntries(book.SheetNames.map((sheet) => [sheet, XLSX.utils.sheet_to_json(book.Sheets[sheet], options)]));
  return { sheets: book.SheetNames, rows };
}

export const importer = () => {
  // Kept out of Alpine's reactive proxies: the file's bytes, or the server's encrypted copy, and its saved choices.
  let source = null;
  let saved = null;
  let questions = [];

  return {
    step: "choose",
    error: "",
    opening: false,
    replacing: false,
    fileName: "",
    sheets: [],
    sheet: "",
    byName: {},
    headerRow: 0,
    firstRow: 0,
    lastRow: -1,
    header: [],
    body: [],
    columns: [],
    passphrase: "",
    passphraseKnown: false,
    confirmation: "",
    rememberIt: true,
    progress: 0,
    ...dropZone(function (files) {
      if (files[0]) this.read(files[0]);
    }),

    init() {
      const { assessmentId, sample, original } = this.$el.dataset;
      Object.assign(this, { assessmentId, sample, original, firstImport: !sample });
      saved = JSON.parse(document.getElementById("results-file-choices")?.textContent ?? "null");
      questions = JSON.parse(document.getElementById("import-questions")?.textContent ?? "[]");
      this.passphrase = (sample && stored(assessmentId)) || "";
      this.passphraseKnown = Boolean(this.passphrase);
      if (this.passphraseKnown) this.rememberIt = remembered(assessmentId);
    },

    get roles() {
      return Object.entries(ROLES);
    },
    get rows() {
      return this.byName[this.sheet] ?? [];
    },
    get studentColumn() {
      return this.columns.find((column) => column.role === "id")?.name ?? "";
    },
    get idIndex() {
      return this.header.indexOf(this.studentColumn);
    },
    get answerColumns() {
      return this.columns.filter((column) => column.role === "answer").map((column) => column.name);
    },
    get students() {
      return this.studentRows.length;
    },
    /** The rows imported as students: those with an ID, never the answer key. */
    get studentRows() {
      return this.body.filter((row) => text(row[this.idIndex]) && !isAnswerKeyRow(row, this.idIndex));
    },
    get summary() {
      const found = `${plural(this.students, "student")}, ${plural(this.answerColumns.length, "question")}`;
      return this.answerKey ? `${found}, answer key` : found;
    },
    /** Why the rows and columns chosen can't be imported, if they can't. */
    get fileProblem() {
      if (!this.studentColumn) return "Choose the column with student IDs.";
      if (!this.students) return "No student IDs in these rows. Check the rows and columns.";
      if (!this.answerColumns.length) return "Choose at least one answer column.";
      const ids = this.studentRows.map((row) => text(row[this.idIndex]));
      if (new Set(ids).size !== ids.length) return "Some student IDs appear more than once. Check the student ID column.";
      return "";
    },
    get marksSummary() {
      const paired = this.columns.filter((column) => column.role === "marks").length;
      return paired ? `${paired} with marks from the file` : "No marks columns";
    },
    /** What replacing the export removes: the questions set up that aren't among the answer columns. */
    get dropped() {
      const keys = questions.filter((key) => !this.answerColumns.includes(key));
      if (!keys.length) return "";
      const names = new Intl.ListFormat("en").format(keys);
      return keys.length === 1 ? `${names} isn't imported, so it's removed with any marking.` : `${names} aren't imported, so they're removed with any marking.`;
    },
    get replaceDetail() {
      const marking = this.dropped ? `${this.dropped} Other marking is kept.` : "Marking is kept.";
      return `Students' answers and imported marks are replaced. ${marking} Students not in the new file are removed, with their adjustments.`;
    },
    get headerPreview() {
      const names = this.header.filter(Boolean);
      return names.slice(0, 4).join(", ") + (names.length > 4 ? ", …" : "");
    },
    /** Rows with values that aren't counted as students, such as "Average" rows, which people should see. */
    get leftOutRows() {
      return [...this.rows.slice(this.headerRow + 1, this.firstRow), ...this.rows.slice(this.lastRow + 1)].filter((row) => !blank(row));
    },
    /** The row ExamSoft adds with the answer key, if the file has one. */
    get answerKey() {
      return this.rows.slice(this.headerRow + 1).find((row) => isAnswerKeyRow(row, this.idIndex));
    },
    get leftOut() {
      return this.leftOutRows.length;
    },
    get rowsPreview() {
      if (!this.body.length) return "No students";
      const range = `${this.label(this.body[0])} to ${this.label(this.body.at(-1))}`;
      const left = this.leftOutRows.map((row) => this.label(row));
      if (!left.length) return range;
      const more = left.length > LEFT_OUT_SHOWN ? ` and ${left.length - LEFT_OUT_SHOWN} more` : "";
      return `${range}. Not included: ${left.slice(0, LEFT_OUT_SHOWN).join(", ")}${more}`;
    },
    get needsPassphrase() {
      return this.firstImport || !this.passphraseKnown;
    },

    /** A row as people would recognise it: its student ID, or else its first value. */
    label(row) {
      if (isAnswerKeyRow(row, this.idIndex)) return "answer key";
      return text(row[this.idIndex]) || row.map(text).find(Boolean) || "";
    },

    chosen(event) {
      const file = event.target.files[0];
      if (file) this.read(file);
    },
    async read(file) {
      this.error = "";
      try {
        const bytes = new Uint8Array(await file.arrayBuffer());
        const found = await readRows(file.name, bytes);
        if (!Object.values(found.rows).some((rows) => rows.some((row) => !blank(row)))) {
          this.error = "That file has no rows.";
          return;
        }
        this.open(file.name, found);
        source = { bytes };
      } catch {
        this.error = "That file couldn't be read. Choose a CSV or Excel file.";
      }
    },
    /** Reopens the current results file from the server's encrypted copy, with the choices it was imported with. */
    async change() {
      this.error = "";
      this.opening = true;
      try {
        const passphrase = await passphraseFor(this.assessmentId, this.sample);
        if (!passphrase) return;
        const response = await fetch(this.original);
        if (!response.ok) throw new Error(response.statusText);
        const encrypted = new Uint8Array(await response.arrayBuffer());
        this.open(saved.name, await readRows(saved.name, await decryptFile(encrypted, passphrase)), saved);
        Object.assign(this, { passphrase, passphraseKnown: true, rememberIt: remembered(this.assessmentId) });
        source = { encrypted };
        this.$root.querySelector("details").open = true;
      } catch {
        this.error = "The file couldn't be opened. Upload it again to change columns.";
      } finally {
        this.opening = false;
      }
    },
    open(name, { sheets, rows }, choices = null) {
      Object.assign(this, { fileName: name, sheets, byName: rows });
      this.sheet = sheets.includes(choices?.sheet) ? choices.sheet : (sheets[0] ?? "");
      if (!choices || !this.restore(choices)) this.detect();
      this.step = "review";
    },
    restore({ header_row, first_row, last_row, student_id_column, answer_columns, marks_columns }) {
      if (header_row === null || !(header_row < first_row && first_row <= last_row && last_row < this.rows.length)) return false;
      this.headerRow = header_row;
      this.header = this.rows[header_row].map(text);
      this.setRows(first_row, last_row);
      this.assign({ studentId: student_id_column, answers: answer_columns, marks: marks_columns });
      return true;
    },
    detect(headerRow = detectHeaderRow(this.rows)) {
      this.headerRow = headerRow;
      this.header = (this.rows[headerRow] ?? []).map(text);
      const below = this.rows.slice(headerRow + 1).filter((row) => !blank(row));
      const { studentId } = detectColumns(this.header, below);
      this.setRows(...detectStudentRows(this.rows, headerRow + 1, this.header.indexOf(studentId)));
      this.assign(detectColumns(this.header, this.body));
    },
    setRows(first, last) {
      Object.assign(this, { firstRow: first, lastRow: last });
      this.body = this.rows.slice(first, last + 1).filter((row) => !blank(row));
      for (const column of this.columns) column.sample = this.sampleOf(column.index);
    },
    /** Gives each column its role: `marks` maps answer columns to their marks columns. */
    assign({ studentId, answers, marks }) {
      const marksFor = new Map(Object.entries(marks).map(([answer, column]) => [column, answer]));
      this.columns = this.header.map((name, index) => ({
        index,
        name,
        role: name === studentId ? "id" : answers.includes(name) ? "answer" : marksFor.has(name) ? "marks" : "ignore",
        marksFor: marksFor.get(name) ?? "",
        sample: this.sampleOf(index),
      }));
    },
    sampleOf(index) {
      return this.body.slice(0, 3).map((row) => text(row[index])).join(", ");
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
      if (column.role === "id") for (const other of this.columns) if (other !== column && other.role === "id") other.role = "ignore";
      if (column.role === "marks" && !column.marksFor) column.marksFor = this.answerColumns[0] ?? "";
    },
    setMarksFor(event) {
      this.columns[Number(event.target.dataset.index)].marksFor = event.target.value;
    },
    restart() {
      source = null;
      Object.assign(this, { step: "choose", error: "", fileName: "", columns: [], header: [], body: [] });
    },

    passphraseProblem() {
      if (this.firstImport) {
        if (this.passphrase.length < MIN_PASSPHRASE) return `Use a passphrase of at least ${MIN_PASSPHRASE} characters.`;
        if (this.passphrase !== this.confirmation) return "The passphrases don't match.";
      } else if (!this.passphrase) {
        return "Enter the student ID passphrase.";
      }
      return "";
    },

    async submit() {
      this.error = this.fileProblem || this.passphraseProblem();
      if (this.error) return;
      if (!this.firstImport && !(await opens(this.passphrase, this.sample))) {
        this.error = "That passphrase doesn't match the one used for this assessment.";
        this.passphraseKnown = false;
        return;
      }
      this.step = "uploading";
      try {
        const idIndex = this.idIndex;
        const keep = this.columns.filter((column) => column.role !== "ignore").map((column) => column.index);
        const rows = [];
        let done = 0;
        for (const row of this.studentRows) {
          const encrypted = await encryptId(text(row[idIndex]), this.passphrase, this.assessmentId);
          rows.push(keep.map((index) => (index === idIndex ? encrypted : String(row[index] ?? ""))));
          this.progress = Math.round((++done / this.students) * 100);
        }
        const original = source.encrypted ?? (await encryptFile(source.bytes, this.passphrase, this.assessmentId));
        await script("papaparse-5.7.0.min.js");
        const marksColumns = Object.fromEntries(
          this.columns.filter((column) => column.role === "marks" && column.marksFor).map((column) => [column.marksFor, column.name]),
        );
        const form = this.$refs.upload;
        const body = new FormData(form);
        body.set("name", this.fileName);
        body.set("results", new Blob([Papa.unparse([keep.map((index) => this.header[index]), ...rows])], { type: "text/csv" }), "results.csv");
        body.set("student_id_column", this.studentColumn);
        body.set("answer_columns", JSON.stringify(this.answerColumns));
        body.set("marks_columns", JSON.stringify(marksColumns));
        if (this.answerKey) {
          const key = this.answerColumns.map((name) => [name, String(this.answerKey[this.header.indexOf(name)] ?? "")]);
          body.set("answer_key", JSON.stringify(Object.fromEntries(key)));
        }
        body.set("original", new Blob([original]), "original");
        body.set("sheet", this.sheet);
        body.set("header_row", this.headerRow);
        body.set("first_row", this.firstRow);
        body.set("last_row", this.lastRow);
        const response = await fetch(form.action, { method: "POST", body, headers: modelHeaders() });
        // A refusal says why in plain text; an error page doesn't.
        if (!response.ok) throw new Error(response.headers.get("Content-Type")?.startsWith("text/plain") ? await response.text() : "");
        remember(this.assessmentId, this.passphrase, this.rememberIt);
        location.assign(response.headers.get("HX-Redirect"));
      } catch (error) {
        this.error = error.message || "The import failed. Try again.";
        this.step = "review";
      }
    },
  };
};
