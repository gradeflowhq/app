// Mapping student IDs to others with a CSV of both, before they're encrypted.

import { cellText, detectColumns } from "./columns.js";

/** An ID as the mapping compares it. */
export const normalizedId = (id) => cellText(id).toLowerCase();

/** The IDs a mapping cell can match: itself, and its last word, as in Moodle's "Participant 1234567". */
const idCandidates = (cell) => {
  const id = normalizedId(cell);
  const lastWord = id.split(/\s+/).at(-1);
  return lastWord === id ? [id] : [id, lastWord];
};

/** A CSV's first row as its header, and its other rows with values. */
export function splitHeader(rows) {
  const [header = [], ...dataRows] = rows.filter((row) => row.some(cellText));
  return { header: header.map(cellText), rows: dataRows };
}

/** The column holding most of `ids`, and the ID column among the rest; blank if no column holds any. */
export function detectMappingColumns(header, rows, ids) {
  const fileIdSet = new Set(ids.map(normalizedId));
  const counts = header.map((_, index) => rows.filter((row) => idCandidates(row[index]).some((id) => fileIdSet.has(id))).length);
  const matchIndex = counts.indexOf(Math.max(...counts));
  if (!counts[matchIndex]) return { matchColumn: "", idColumn: "" };
  const otherColumns = header.filter((_, index) => index !== matchIndex);
  const otherRows = rows.map((row) => row.filter((_, index) => index !== matchIndex).map(cellText));
  const idColumn = detectColumns(otherColumns, otherRows).studentId ?? otherColumns[0] ?? "";
  return { matchColumn: header[matchIndex], idColumn };
}

/** Each ID in `matchColumn`, and its last word, to the ID beside it in `idColumn`; an ID's first row counts. */
export function idMap(header, rows, matchColumn, idColumn) {
  const [matchIndex, idIndex] = [header.indexOf(matchColumn), header.indexOf(idColumn)];
  const map = new Map();
  for (const row of rows) {
    const newId = cellText(row[idIndex]);
    for (const id of newId ? idCandidates(row[matchIndex]) : []) if (id && !map.has(id)) map.set(id, newId);
  }
  return map;
}
