// A zip of submissions under review: the pattern finding each path's student and file, and each file's question.

import { counted, formatList } from "./words.js";
import {
  MAX_ZIP_ENTRY_BYTES,
  answerFromFiles,
  commonFirst,
  fileQuestionsFor,
  filesForQuestion,
  isCommon,
  matchPaths,
  naturalOrder,
  pathTokens,
  patternFromTokens,
  patternRegex,
  questionKey,
  samplePaths,
  suggestPattern,
  topLevelEntries,
} from "./zip-files.js";

const MAX_EXAMPLE_PATHS = 4;
const MAX_UNMATCHED_SHOWN = 20;

/** A zip under review, opened with its saved settings if it's reopened; `questionKeys` are the questions set up. */
export function zipSource({ texts, skipped }, savedSettings, questionKeys) {
  // Kept out of Alpine's proxies: the zip's thousands of files.
  const paths = [...texts.keys()];
  const examplePaths = samplePaths(paths).slice(0, MAX_EXAMPLE_PATHS);

  const source = {
    isZip: true,
    pattern: "",
    /** What the pattern finds, frozen to keep it out of Alpine's proxies. */
    found: null,
    fileQuestions: {},
    patternDraft: "",
    patternError: "",
    isEditingPattern: false,
    isShowingUnmatched: false,
    isShowingOtherFiles: false,

    /** The files table's rows: common files first, then by name. */
    get fileRows() {
      const { counts, names, submissions } = this.found;
      return names.map((name) => {
        const isCommonName = isCommon(counts.get(name), submissions.size);
        return { name, question: this.fileQuestions[name], studentCount: counts.get(name), isCommon: isCommonName, isFolder: name.endsWith("/") };
      });
    },
    get commonFileRows() {
      return this.fileRows.filter((row) => row.isCommon);
    },
    /** Files with no question when the pattern was applied; they stay listed apart once given one. */
    get noQuestionRows() {
      return this.fileRows.filter((row) => this.found.noQuestionNames.has(row.name));
    },
    get otherFileRows() {
      return this.fileRows.filter((row) => !row.isCommon && !this.found.noQuestionNames.has(row.name));
    },
    get noQuestionCount() {
      return this.noQuestionRows.filter((row) => !row.question).length;
    },
    /** The questions a file or folder can answer: those set up, those of other files, and its own. */
    questionsFor(name) {
      const keys = new Set([...questionKeys, ...Object.values(this.fileQuestions).filter(Boolean), questionKey(name)]);
      return [...keys].toSorted(naturalOrder);
    },
    get skippedNote() {
      const { system, binary, large } = skipped;
      const phrases = [
        system && counted(system, "hidden or system file"),
        binary && `${counted(binary, "file")} that ${binary === 1 ? "isn't" : "aren't"} text`,
        large && `${counted(large, "file")} over ${MAX_ZIP_ENTRY_BYTES / 2 ** 20} MB`,
      ].filter(Boolean);
      return phrases.length ? `Left out ${formatList(phrases)}.` : "";
    },
    get unmatchedNote() {
      const count = this.found.unmatchedPaths.length;
      return count ? `${counted(count, "file")} ${count === 1 ? "doesn't" : "don't"} fit` : "";
    },
    get shownUnmatchedPaths() {
      const { unmatchedPaths } = this.found;
      const hidden = unmatchedPaths.length - MAX_UNMATCHED_SHOWN;
      return hidden > 0 ? [...unmatchedPaths.slice(0, MAX_UNMATCHED_SHOWN), `and ${hidden} more`] : unmatchedPaths;
    },
    get otherFilesLabel() {
      return `${counted(this.otherFileRows.length, "more file")} matched by name or set by hand`;
    },
    /** The example paths as runs of student, file and other tokens. */
    get examples() {
      return examplePaths.map((path, exampleIndex) => {
        const runs = [];
        for (const token of this.exampleTokens(exampleIndex)) {
          const last = runs.at(-1);
          if (last?.role === token.role) last.tokens.push(token);
          else runs.push({ key: token.key, role: token.role, tokens: [token] });
        }
        return { exampleIndex, path, runs };
      });
    },
    /** Example `exampleIndex`'s tokens, each with its role. */
    exampleTokens(exampleIndex) {
      const path = examplePaths[exampleIndex];
      const groups = patternRegex(this.pattern).exec(path)?.indices.groups;
      let end = 0;
      let studentIndex = -1;
      let fileStartIndex = -1;
      return pathTokens(path).map((token, index) => {
        const start = end;
        end += token.text.length;
        const role = !groups ? "" : start >= groups.student[0] && end <= groups.student[1] ? "student" : start >= groups.file[0] ? "file" : "";
        if (role === "student" && studentIndex < 0) studentIndex = index;
        if (role === "file" && fileStartIndex < 0 && !token.isSeparator) fileStartIndex = index;
        const isFileStart = fileStartIndex === index;
        const key = `${exampleIndex}-${index}`;
        return { ...token, index, exampleIndex, role, key, isFileStart, canStartFile: studentIndex >= 0 && index > studentIndex && !isFileStart };
      });
    },

    get importedQuestions() {
      return [...new Set(Object.values(this.fileQuestions).filter(Boolean))].toSorted(naturalOrder);
    },
    get ids() {
      return [...this.found.submissions.keys()];
    },
    get problem() {
      if (!this.ids.length) return "No files fit the pattern. Check students and files.";
      if (!this.importedQuestions.length) return "Choose a question for at least one file.";
      return "";
    },
    get extrasSummary() {
      return this.noQuestionCount ? `${counted(this.noQuestionCount, "file")} with no question` : counted(this.found.fileCount, "file");
    },
    get needsReview() {
      return this.noQuestionRows.length > 0 || this.found.unmatchedPaths.length > 0;
    },
    /** Each student's answers joined from their files, the ID first. */
    answersTable() {
      const { entries, counts, submissions } = this.found;
      const common = new Set([...counts.keys()].filter((name) => isCommon(counts.get(name), submissions.size)));
      const rows = [...entries].map(([id, studentEntries]) => [
        id,
        ...this.importedQuestions.map((question) => {
          const files = filesForQuestion(studentEntries, question, this.fileQuestions, common).map(({ file, path }) => [file, texts.get(path)]);
          try {
            return files.length ? answerFromFiles(files) : "";
          } catch (error) {
            throw new Error(`${id}: ${error.message}`);
          }
        }),
      ]);
      return { header: ["Student ID", ...this.importedQuestions], rows };
    },
    formFields() {
      return { file_pattern: this.pattern, file_questions: JSON.stringify(this.fileQuestions) };
    },

    /** Finds students and files with `pattern`, keeping the questions files already have. */
    applyPattern(pattern, savedQuestions = this.fileQuestions) {
      const { submissions, unmatchedPaths } = matchPaths(patternRegex(pattern), paths);
      const entries = topLevelEntries(submissions);
      const counts = new Map();
      for (const studentEntries of entries.values()) for (const name of studentEntries.keys()) counts.set(name, (counts.get(name) ?? 0) + 1);
      const fileQuestions = fileQuestionsFor(counts, submissions.size, savedQuestions);
      const names = [...counts.keys()].sort(commonFirst(counts, submissions.size));
      const noQuestionNames = new Set(names.filter((name) => !fileQuestions[name]));
      this.found = Object.freeze({ submissions, unmatchedPaths, entries, counts, names, noQuestionNames, fileCount: paths.length - unmatchedPaths.length });
      Object.assign(this, { pattern, fileQuestions, isEditingPattern: false, patternError: "" });
    },
    /** Makes token `index` of an example the student; the file starts where it did if that's after it. */
    setStudentToken(exampleIndex, index) {
      const tokens = this.exampleTokens(exampleIndex);
      const fileStartIndex = tokens.find((token) => token.isFileStart)?.index ?? -1;
      const next = tokens.find((token) => token.index > index && !token.isSeparator)?.index ?? -1;
      const start = fileStartIndex > index ? fileStartIndex : next;
      if (start >= 0) this.applyPattern(patternFromTokens(tokens, index, start, paths));
    },
    /** Makes the file start at token `index` of an example. */
    setFileStart(exampleIndex, index) {
      const tokens = this.exampleTokens(exampleIndex);
      const studentIndex = tokens.find((token) => token.role === "student")?.index ?? -1;
      if (studentIndex >= 0 && index > studentIndex) this.applyPattern(patternFromTokens(tokens, studentIndex, index, paths));
    },
    editPattern() {
      Object.assign(this, { patternDraft: this.pattern, patternError: "", isEditingPattern: true });
    },
    savePattern() {
      try {
        this.applyPattern(this.patternDraft);
      } catch (error) {
        this.patternError = error.message;
      }
    },
    cancelPattern() {
      Object.assign(this, { isEditingPattern: false, patternError: "" });
    },
    setQuestion(event) {
      this.fileQuestions = { ...this.fileQuestions, [event.target.dataset.name]: event.target.value };
    },
  };
  source.applyPattern(savedSettings?.file_pattern || suggestPattern(paths), savedSettings?.file_questions ?? {});
  return source;
}
