// Importing a submissions file. It's read in the browser; only the encrypted student IDs and the answers are uploaded
// readable, as CSV, and the whole file encrypted, to be reopened. The file under review is `source`, a spreadsheet
// (import-table.js) or a zip (import-zip.js).

import { cellText } from "./columns.js";
import { assessmentKeysFor, missingCourseKeyMessage } from "./course-keys.js";
import { decryptFile, encryptFile, encryptId } from "./crypto.js";
import { confirmAction } from "./dialogs.js";
import { dropZone } from "./drop.js";
import { idMapping } from "./import-ids.js";
import { isEmptyTable, readTable, tableSource } from "./import-table.js";
import { zipSource } from "./import-zip.js";
import { readModelHeaders } from "./models.js";
import { loadScript } from "./vendor.js";
import { counted, formatList } from "./words.js";
import { readZip } from "./zip-files.js";

/** Resolves once the page has drawn a frame. */
const nextFrame = () => new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve)));

/** A zip's files or a spreadsheet's rows. */
const readSubmissions = async (name, bytes, onProgress) =>
  /\.zip$/i.test(name) ? { zip: await readZip(bytes, onProgress) } : { table: await readTable(name, bytes) };

export const importer = () => {
  // Kept out of Alpine's proxies: the file's bytes, or the server's encrypted copy, and its saved settings.
  let fileContents = null;
  let savedSettings = null;
  let reopenedSnapshot = null; // a reopened file's choices as it opened; null for a new file
  let skipLeaveWarning = false;

  return {
    step: "choose",
    error: "",
    opening: false,
    isReading: false,
    readProgress: { done: 0, total: 0 },
    isFindingStudents: false,
    replacing: false,
    fileName: "",
    isReviewOpen: false,
    progress: 0,
    questionKeys: [],
    /** The file under review: `tableSource` or `zipSource`. */
    source: null,
    idMapping: null,
    ...dropZone(function (files) {
      if (files[0]) this.read(files[0]);
    }),

    init() {
      savedSettings = JSON.parse(document.getElementById("import-settings")?.textContent ?? "null");
      this.questionKeys = JSON.parse(document.getElementById("import-questions")?.textContent ?? "[]");
      this.idMapping = idMapping(() => this.fileIds);
    },
    get isZip() {
      return this.source?.isZip ?? false;
    },
    get importedQuestions() {
      return this.source?.importedQuestions ?? [];
    },
    /** The student IDs in the file, before any mapping. */
    get fileIds() {
      return this.source?.ids ?? [];
    },
    get summary() {
      const counts = `${counted(this.fileIds.length, "student")}, ${counted(this.importedQuestions.length, "question")}`;
      const extras = [this.source?.extrasSummary, this.idMapping?.note].filter(Boolean).join(" · ");
      return [!this.isZip && this.source?.answerKey ? `${counts}, answer key` : counts, extras].filter(Boolean).join(" · ");
    },
    /** Why the choices made can't be imported, if they can't. */
    get fileProblem() {
      if (!this.source) return "";
      if (this.source.problem) return this.source.problem;
      const ids = this.fileIds.map((id) => this.idMapping.mappedId(id));
      if (new Set(ids).size !== ids.length) {
        return this.idMapping.mapping ? "Some students map to the same ID. Check the ID mapping." : "Some student IDs appear more than once. Check the student ID column.";
      }
      return "";
    },
    /** What replacing the submissions file removes: the questions set up that aren't imported. */
    get removedQuestionsNote() {
      const keys = this.questionKeys.filter((key) => !this.importedQuestions.includes(key));
      if (!keys.length) return "";
      return keys.length === 1 ? `${formatList(keys)} isn't imported, so it's removed with any marking.` : `${formatList(keys)} aren't imported, so they're removed with any marking.`;
    },
    get replaceDetail() {
      const marking = this.removedQuestionsNote ? `${this.removedQuestionsNote} Other marking is kept.` : "Marking is kept.";
      return `Students' answers and imported marks are replaced. ${marking} Students not in the new file are removed, with their adjustments.`;
    },
    get readPercent() {
      const { done, total } = this.readProgress;
      return total ? Math.round((done / total) * 100) : 0;
    },
    get readStatus() {
      const { done, total } = this.readProgress;
      if (this.isFindingStudents) return "Finding students and files…";
      return total ? `Reading ${done.toLocaleString("en")} of ${counted(total, "file")}…` : "Reading the file…";
    },

    /** The choices made, to tell whether a reopened file changed. */
    get choicesSnapshot() {
      return JSON.stringify([this.source?.formFields(), this.idMapping?.columns]);
    },
    /** Whether leaving now loses a new file or changes to a reopened one. */
    get hasUnsavedChanges() {
      if (skipLeaveWarning || this.step === "choose") return false;
      return this.step === "uploading" || reopenedSnapshot === null || this.choicesSnapshot !== reopenedSnapshot;
    },
    warnOnUnload(event) {
      if (this.hasUnsavedChanges) event.preventDefault();
    },
    /** Asks before an in-app link replaces this page with changes unsaved, and follows it if confirmed. */
    async askBeforeNavigating(event) {
      const { ctx } = event.detail;
      if (!this.hasUnsavedChanges || ctx.request.headers["HX-Request-Type"] !== "full") return;
      event.preventDefault();
      const detail = this.step === "uploading" ? "The import stops." : "Your choices for this file are lost.";
      if (!(await confirmAction("Leave without importing?", detail, "Leave"))) return;
      skipLeaveWarning = true;
      ctx.sourceElement.click();
    },

    fileChosen(event) {
      const file = event.target.files[0];
      if (file) this.read(file);
    },
    async read(file) {
      const maxBytes = Number(this.$root.dataset.maxFileBytes);
      if (file.size > maxBytes) {
        this.error = `That file is too large to import. The limit is ${maxBytes / 2 ** 20} MB.`;
        return;
      }
      Object.assign(this, { error: "", fileName: file.name, isReading: true, readProgress: { done: 0, total: 0 }, isFindingStudents: false });
      try {
        const bytes = await file.bytes();
        const contents = await readSubmissions(file.name, bytes, (done, total) => {
          this.readProgress = { done, total };
          return nextFrame();
        });
        if (contents.zip) {
          this.isFindingStudents = true;
          await nextFrame(); // the work that follows blocks the page
        }
        if (contents.table && isEmptyTable(contents.table)) {
          this.error = "That file has no rows.";
          return;
        }
        if (contents.zip && !contents.zip.texts.size) {
          this.error = "That zip has no text files.";
          return;
        }
        this.open(file.name, contents);
        fileContents = { bytes };
        reopenedSnapshot = null;
      } catch {
        this.error = "That file couldn't be read. Choose a CSV, Excel or zip file.";
      } finally {
        this.isReading = false;
      }
    },
    /** Null after showing why the keys are missing. */
    async keys(options = {}) {
      const assessmentElement = this.$root.closest("[data-assessment]");
      const keys = await assessmentKeysFor(assessmentElement, options).catch(() => null);
      if (!keys) this.error = missingCourseKeyMessage(assessmentElement);
      return keys;
    },
    /** Reopens the current submissions file from the server's encrypted copy, with the choices it was imported with. */
    async reopenFile() {
      this.error = "";
      this.opening = true;
      try {
        const keys = await this.keys();
        if (!keys) return;
        const encrypted = await this.fetchBytes(this.$root.dataset.originalUrl);
        this.open(savedSettings.name, await readSubmissions(savedSettings.name, await decryptFile(encrypted, keys)), savedSettings);
        fileContents = { encrypted };
        if (savedSettings.id_mapping) await this.idMapping.reopen(await this.fetchBytes(this.$root.dataset.idMappingUrl), keys, savedSettings.id_mapping);
        this.isReviewOpen = true;
        reopenedSnapshot = this.choicesSnapshot;
      } catch {
        this.error = "The file couldn't be opened. Upload it again to change your choices.";
      } finally {
        this.opening = false;
      }
    },
    async fetchBytes(url) {
      const response = await fetch(url);
      if (!response.ok) throw new Error(response.statusText);
      return response.bytes();
    },
    open(name, contents, settings = null) {
      this.fileName = name;
      this.idMapping.remove();
      this.source = contents.zip ? zipSource(contents.zip, settings, this.questionKeys) : tableSource(contents.table, settings);
      this.isReviewOpen = this.source.needsReview;
      this.step = "review";
    },
    restart() {
      fileContents = null;
      this.idMapping.remove();
      Object.assign(this, { step: "choose", error: "", fileName: "", source: null });
    },
    async chooseIdMapping(event) {
      const [file] = event.target.files;
      event.target.value = "";
      if (!file) return;
      this.error = "";
      try {
        await this.idMapping.open(file);
      } catch {
        this.error = "That ID mapping file couldn't be read. Choose a CSV file.";
      }
    },

    async submit() {
      this.error = this.fileProblem;
      if (this.error) return;
      const keys = await this.keys({ create: true });
      if (!keys) return;
      this.step = "uploading";
      try {
        const { header, rows } = this.source.answersTable();
        let encryptedCount = 0;
        for (const row of rows) {
          row[0] = await encryptId(this.idMapping.mappedId(cellText(row[0])), keys);
          this.progress = Math.round((++encryptedCount / rows.length) * 100);
        }
        const original = fileContents.encrypted ?? (await encryptFile(fileContents.bytes, keys));
        await loadScript("papaparse-5.7.0.min.js");
        const form = this.$refs.upload;
        const body = new FormData(form);
        const fields = {
          name: this.fileName,
          answers_csv: new Blob([Papa.unparse([header, ...rows])], { type: "text/csv" }),
          student_id_column: header[0],
          answer_columns: JSON.stringify(this.importedQuestions),
          original: new Blob([original]),
          ...this.source.formFields(),
          ...(await this.idMapping.formFields(keys)),
        };
        for (const [field, value] of Object.entries(fields)) body.set(field, value);
        const response = await fetch(form.action, { method: "POST", body, headers: await readModelHeaders() });
        // A refusal says why in plain text; an error page doesn't.
        if (!response.ok) throw new Error(response.headers.get("Content-Type")?.startsWith("text/plain") ? await response.text() : "");
        skipLeaveWarning = true;
        location.assign(response.headers.get("HX-Redirect"));
      } catch (error) {
        this.error = error.message || "The import failed. Try again.";
        this.step = "review";
      }
    },
  };
};
