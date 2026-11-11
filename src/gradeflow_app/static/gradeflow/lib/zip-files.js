// A zip of submissions: a pattern such as `submissions/{student}/{file}` finds each path's student and file, and each
// student's top-level files and folders answer questions.

import { decodeText } from "./text-files.js";
import { naturalOrder } from "./words.js";

const IGNORED_PATH = /(?:^|\/)(?:__MACOSX|__pycache__|\.[^/]*)(?:\/|$)|\/$/;

/** Whether a zip entry is a folder, macOS metadata, a cache or a hidden file. */
export const isIgnoredPath = (path) => IGNORED_PATH.test(path);

export const MAX_ZIP_ENTRY_BYTES = 2 ** 20;

/** A zip's text files by path, in order, and how many it left out: hidden, binary or over `MAX_ZIP_ENTRY_BYTES`.
 * Awaits `onProgress(read, total)` every 4% of the entries. */
export async function readZip(bytes, onProgress = () => {}) {
  const { ZipReader, Uint8ArrayReader, Uint8ArrayWriter } = await import("../../vendor/zip.js-2.24.0/zip.min.js");
  const reader = new ZipReader(new Uint8ArrayReader(bytes), { useWebWorkers: false });
  const texts = new Map();
  const skipped = { system: 0, binary: 0, large: 0 };
  try {
    const entries = await reader.getEntries();
    const progressInterval = Math.ceil(entries.length / 25);
    for (const [index, entry] of entries.entries()) {
      if (index % progressInterval === 0) await onProgress(index, entries.length);
      if (entry.directory) continue;
      if (isIgnoredPath(entry.filename)) skipped.system++;
      else if (entry.uncompressedSize > MAX_ZIP_ENTRY_BYTES) skipped.large++;
      else {
        const text = decodeText(await entry.getData(new Uint8ArrayWriter()));
        if (text === null) skipped.binary++;
        else texts.set(entry.filename, text);
      }
    }
    await onProgress(entries.length, entries.length);
  } finally {
    await reader.close();
  }
  return { texts: new Map([...texts].sort(([a], [b]) => naturalOrder(a, b))), skipped };
}

/** A path split at `/` and `_`, separators kept. */
export const pathTokens = (path) =>
  path
    .split(/([/_])/)
    .filter(Boolean)
    .map((text) => ({ text, isSeparator: text === "/" || text === "_" }));

const isDigits = (text) => /^\d+$/.test(text);

/** Whether a token could be an ID: digits, perhaps after letters, or an email address. */
const looksLikeId = (text) => /^[A-Za-z]*\d+[A-Za-z]*$|^[^/@\s]+@[^/@\s]+$/.test(text);

/** A path's separators and token kinds, its file name counting as one token. */
function pathShape(path) {
  const kinds = pathTokens(path).map(({ text, isSeparator }) =>
    isSeparator ? text : looksLikeId(text) ? "id" : "text",
  );
  const lastId = kinds.findLastIndex((kind) => !["text", "/", "_"].includes(kind));
  const afterLastId = kinds.slice(lastId + 1).filter((kind) => kind !== "_");
  return [...kinds.slice(0, lastId + 1), ...afterLastId].join("").replace(/(?:text)+/g, "text");
}

/** One path of each shape, the most common shape first. */
export function samplePaths(paths) {
  const byShape = Map.groupBy(paths, pathShape);
  return [...byShape.values()].sort((a, b) => b.length - a.length).map(([path]) => path);
}

const isMissingFromSomePath = (text, paths) => paths.some((path) => !path.includes(text));

const isDigitsOrDashes = (text) => /^[\d-]+$/.test(text);

/** A pattern for `paths`. The student is the first ID-like token that varies; the file starts after the tokens that
 * follow it alike in most paths (Moodle's `assignsubmission_file/`, Canvas's submission number). */
export function suggestPattern(paths) {
  const tokens = pathTokens(samplePaths(paths)[0] ?? "");
  const candidateIndices = tokens.flatMap((token, index) =>
    token.isSeparator || index === tokens.length - 1 ? [] : [index],
  );
  const studentIndex =
    candidateIndices.find(
      (index) => looksLikeId(tokens[index].text) && isMissingFromSomePath(tokens[index].text, paths),
    ) ??
    candidateIndices.find((index) => isMissingFromSomePath(tokens[index].text, paths)) ??
    candidateIndices[0] ??
    0;
  // What follows the student and its separator in each path.
  const regex = patternRegex(
    patternFromTokens(tokens, studentIndex, Math.min(studentIndex + 2, tokens.length - 1), paths),
  );
  const remainders = paths.flatMap((path) => regex.exec(path)?.groups.file ?? []).map(pathTokens);
  let sharedLength = 0;
  for (;;) {
    // This token and the separator after it, in the paths that go on past them.
    const pairs = remainders
      .filter((tokens) => tokens.length > sharedLength + 2)
      .map((tokens) => [tokens[sharedLength], tokens[sharedLength + 1]]);
    if (!pairs.length || pairs.length < remainders.length / 2) break;
    const [token, separator] = pairs[0];
    const isAlike = ([other, next]) =>
      next.isSeparator &&
      next.text === separator.text &&
      (other.text === token.text || (isDigitsOrDashes(other.text) && isDigitsOrDashes(token.text)));
    if (!separator.isSeparator || !pairs.every(isAlike)) break;
    sharedLength += 2;
  }
  return patternFromTokens(tokens, studentIndex, Math.min(studentIndex + 2 + sharedLength, tokens.length - 1), paths);
}

/** The pattern a path's `tokens` make with token `studentIndex` as the student and the file starting at token
 * `fileStartIndex`. Text every path has stays as written; other tokens become `{number}` or `*`. */
export function patternFromTokens(tokens, studentIndex, fileStartIndex, paths) {
  const prefix = tokens.slice(0, studentIndex);
  const prefixText = prefix.map(({ text }) => text).join("");
  let pattern = prefixText;
  if (!paths.every((path) => path.startsWith(prefixText))) {
    const separators = prefix.slice(prefix.findLastIndex((token) => !token.isSeparator) + 1);
    pattern = `*${separators.map(({ text }) => text).join("")}`;
  }
  // A folder name needs no shape; in a file name, digits tell the student from the tokens around it.
  const isFolder =
    tokens[studentIndex + 1]?.text === "/" && (studentIndex === 0 || tokens[studentIndex - 1].text === "/");
  pattern += !isFolder && isDigits(tokens[studentIndex].text) ? "{student:number}" : "{student}";
  for (const { text, isSeparator } of tokens.slice(studentIndex + 1, fileStartIndex)) {
    if (isSeparator || paths.every((path) => path.includes(text))) pattern += text;
    else pattern += isDigits(text) ? "{number}" : "*";
  }
  return `${pattern}{file}`;
}

const STUDENT_PLACEHOLDER = String.raw`\{student(?::number)?\}`;
const PLACEHOLDER = new RegExp(String.raw`${STUDENT_PLACEHOLDER}|\{number\}|\{file\}|\*`, "g");

/** The regular expression for `pattern`, with `student` and `file` groups. Throws, saying why, for an invalid one. */
export function patternRegex(pattern) {
  const studentCount = [...pattern.matchAll(new RegExp(STUDENT_PLACEHOLDER, "g"))].length;
  if (/\{student:(?!number\})/.test(pattern)) throw new Error("Write the student as {student} or {student:number}.");
  if (!studentCount) throw new Error("Add {student} to the pattern.");
  if (studentCount > 1) throw new Error("Use {student} once.");
  if (pattern.split("{file}").length !== 2 || !pattern.endsWith("{file}")) {
    throw new Error("End the pattern with {file}, once.");
  }
  let source = "";
  let end = 0;
  for (const match of pattern.matchAll(PLACEHOLDER)) {
    source += RegExp.escape(pattern.slice(end, match.index));
    const [placeholder] = match;
    if (placeholder.startsWith("{student"))
      source += `(?<student>${placeholder === "{student:number}" ? "\\d+" : "[^/]+?"})`;
    else if (placeholder === "{number}") source += "\\d+";
    else if (placeholder === "{file}") source += "(?<file>.+)";
    else source += ".*?";
    end = match.index + placeholder.length;
  }
  return new RegExp(`^${source}$`, "d");
}

/** Each student's files as `{ file, path }`, `file` being what follows the student, and the paths that don't fit. */
export function matchPaths(regex, paths) {
  const submissions = new Map();
  const unmatchedPaths = [];
  for (const path of paths) {
    const groups = regex.exec(path)?.groups;
    if (!groups) {
      unmatchedPaths.push(path);
      continue;
    }
    if (!submissions.has(groups.student)) submissions.set(groups.student, []);
    submissions.get(groups.student).push({ file: groups.file, path });
  }
  return { submissions, unmatchedPaths };
}

const loneFolder = (files) => {
  const folder = files[0]?.file.match(/^[^/]+\//)?.[0];
  return folder && files.every(({ file }) => file.startsWith(folder)) ? folder : "";
};

/** Each student's top-level files and folders (named with a trailing `/`) with their files. A folder holding all of a
 * student's files is unwrapped, unless most students have it. */
export function topLevelEntries(submissions) {
  const loneFolderCounts = Map.groupBy([...submissions.values()].map(loneFolder).filter(Boolean), (folder) => folder);
  const isAnswerFolder = (folder) => (loneFolderCounts.get(folder)?.length ?? 0) >= submissions.size / 2;
  return new Map(
    [...submissions].map(([id, files]) => {
      let entries = files;
      for (let folder = loneFolder(entries); folder && !isAnswerFolder(folder); folder = loneFolder(entries)) {
        entries = entries.map((entry) => ({ ...entry, file: entry.file.slice(folder.length) }));
      }
      return [id, Map.groupBy(entries, ({ file }) => file.match(/^[^/]+\//)?.[0] ?? file)];
    }),
  );
}

/** `name` without case, punctuation, spaces or a repeated extension, and with `withoutCopyNumber`, without a copy's
 * number ("-1", " (1)"). */
function normalizedName(name, { withoutCopyNumber = false } = {}) {
  const lower = name.toLowerCase().replace(/\/$/, "");
  const extension = lower.match(/\.[a-z\d]+$/)?.[0] ?? "";
  let stem = lower.replace(new RegExp(`(?:${RegExp.escape(extension)})+$`), "");
  if (withoutCopyNumber) stem = stem.replace(/(?:-\d+| \(\d+\))$/, "");
  return stem.replace(/[^a-z\d]/g, "") + extension;
}

/** A file's name without its extension, or a folder's name, as a question key. */
export const questionKey = (name) =>
  name
    .replace(/\/$/, "")
    .replace(/\.[^.]+$/, "")
    .trim() || name;

/** Whether a file or folder is in at least half the submissions. */
export const isCommon = (count, submissionCount) => count >= submissionCount / 2;

/** Common names first, then by name. */
const commonFirst = (counts, submissionCount) => (a, b) =>
  isCommon(counts.get(b), submissionCount) - isCommon(counts.get(a), submissionCount) || naturalOrder(a, b);

/** The question each top-level file or folder answers, "" for none: `saved`'s, else its own if it's common, else that
 * of the one common name it misspells. */
export function fileQuestionsFor(counts, submissionCount, saved = {}) {
  const common = [...counts.keys()].filter((name) => isCommon(counts.get(name), submissionCount));
  const commonByName = Map.groupBy(common, (name) => normalizedName(name));
  const misspelled = (name) => {
    const matches =
      commonByName.get(normalizedName(name)) ??
      commonByName.get(normalizedName(name, { withoutCopyNumber: true })) ??
      [];
    return matches.length === 1 ? questionKey(matches[0]) : "";
  };
  return Object.fromEntries(
    [...counts.keys()].map((name) => [
      name,
      saved[name] ?? (isCommon(counts.get(name), submissionCount) ? questionKey(name) : misspelled(name)),
    ]),
  );
}

/** A file's place in its answer: `main.py` or `__main__.py`, then the file named after the question, then other
 * Python files. */
const runOrder = (file, question) =>
  /^(?:__main__|main)\.py$/.test(file) ? 0 : questionKey(file) === question ? 1 : file.endsWith(".py") ? 2 : 3;

/** The files answering `question` in a submission, in run order: its common files and folders, or if none, its
 * others. A folder that is the whole answer has its files named from inside it. */
export function filesForQuestion(entries, question, fileQuestions, common) {
  const names = [...entries.keys()].filter((name) => fileQuestions[name] === question);
  const commonNames = names.filter((name) => common.has(name));
  const questionNames = commonNames.length ? commonNames : names;
  const isRoot = questionNames.length === 1 && questionNames[0].endsWith("/");
  return questionNames
    .flatMap((name) =>
      entries.get(name).map((entry) => (isRoot ? { ...entry, file: entry.file.slice(name.length) } : entry)),
    )
    .toSorted((a, b) => runOrder(a.file, question) - runOrder(b.file, question) || naturalOrder(a.file, b.file));
}

const isFileMarker = (line) =>
  line.length > 6 && line.startsWith("-- ") && line.endsWith(" --") && line.slice(3, -3).trim();

/** One answer from `[file, text]` pairs: a single file as it is, several as txtar. Throws for a line that would read
 * as a file marker. */
export function answerFromFiles(files) {
  for (const [file, text] of files) {
    const lines = text.split("\n");
    const markerLine = files.length === 1 ? (isFileMarker(lines[0]) ? 0 : -1) : lines.findIndex(isFileMarker);
    if (markerLine >= 0) throw new Error(`${file} line ${markerLine + 1} looks like a file marker.`);
  }
  if (files.length === 1) return files[0][1];
  return files.map(([file, text]) => `-- ${file} --\n${text.endsWith("\n") ? text : `${text}\n`}`).join("");
}

/** What `pattern` finds in `paths`: each student's files and top-level entries, and the question each top-level name
 * answers, `savedQuestions` first. */
export function findSubmissions(pattern, paths, savedQuestions = {}) {
  const { submissions, unmatchedPaths } = matchPaths(patternRegex(pattern), paths);
  const entries = topLevelEntries(submissions);
  const counts = new Map();
  for (const studentEntries of entries.values()) {
    for (const name of studentEntries.keys()) counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  const fileQuestions = fileQuestionsFor(counts, submissions.size, savedQuestions);
  const names = [...counts.keys()].sort(commonFirst(counts, submissions.size));
  const noQuestionNames = new Set(names.filter((name) => !fileQuestions[name]));
  const fileCount = paths.length - unmatchedPaths.length;
  return { submissions, unmatchedPaths, entries, counts, names, fileQuestions, noQuestionNames, fileCount };
}

/** Each student's answers to `questions`, the ID first, from the files `texts` holds. Throws, naming the student, for
 * an answer that can't be joined. */
export function submissionAnswers({ entries, counts, submissions }, fileQuestions, questions, texts) {
  const common = new Set([...counts.keys()].filter((name) => isCommon(counts.get(name), submissions.size)));
  return [...entries].map(([id, studentEntries]) => [
    id,
    ...questions.map((question) => {
      const files = filesForQuestion(studentEntries, question, fileQuestions, common).map(({ file, path }) => [
        file,
        texts.get(path),
      ]);
      try {
        return files.length ? answerFromFiles(files) : "";
      } catch (error) {
        throw new Error(`${id}: ${error.message}`);
      }
    }),
  ]);
}
