// Spreadsheet cells, and the student IDs in them.

export const cellText = (cell) => String(cell ?? "").trim();

export const isBlankRow = (row) => !row.some(cellText);

export const normalizedId = (id) => cellText(id).toLowerCase();
