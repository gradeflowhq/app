/** A count with its noun: "1 student", "3 students", "6,452 files". */
export const counted = (count, word, words = `${word}s`) =>
  `${Number.isInteger(count) ? count.toLocaleString("en") : count} ${count === 1 ? word : words}`;

/** Items as a sentence lists them: "a, b, and c". */
export const formatList = (items) => new Intl.ListFormat("en").format(items);

/** Whether lowercase `text` contains every word of `query`. */
export const matches = (text, query) =>
  query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => text.includes(word));
