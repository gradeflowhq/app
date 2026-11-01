/** A count with its noun: "1 student", "3 students". */
export const plural = (count, word, words = `${word}s`) => `${count} ${count === 1 ? word : words}`;

/** Whether `text`, in lowercase, has every word of `query`, as people search a list by typing. */
export const matches = (text, query) =>
  query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => text.includes(word));
