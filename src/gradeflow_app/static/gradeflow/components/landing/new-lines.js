// The lines each change adds to a marking's summary, marked `data-new` for the demos to highlight.

const descriptionLines = (description) => [...description.querySelectorAll("li, p")];
const codeLines = (description) =>
  [...description.querySelectorAll("pre")].flatMap((pre) => pre.textContent.split("\n"));

// A line's text, without the lists inside it.
function textWithoutNestedLists(line) {
  const copy = line.cloneNode(true);
  for (const list of copy.querySelectorAll("ul, ol")) list.remove();
  return copy.textContent.trim();
}

// Puts each line of `pre`'s code in its own element, marking those not in `previousLines` as new.
function markNewCodeLines(pre, previousLines) {
  const code = pre.querySelector("code");
  code.replaceChildren(
    ...code.textContent.split("\n").flatMap((line, index) => {
      const element = Object.assign(document.createElement("span"), { textContent: line });
      element.toggleAttribute("data-new", !previousLines.has(line));
      return index ? ["\n", element] : [element];
    }),
  );
}

// Marks the lines of each of a marking's `summaries`, code included, that the summary before doesn't have.
export function markSummaryChanges(summaries) {
  summaries.slice(1).forEach((summary, index) => {
    const description = summary.querySelector(".prose-core");
    if (!description) return;
    const previous = summaries[index].querySelector(".prose-core");
    const lines = new Set(previous ? descriptionLines(previous).map(textWithoutNestedLists) : []);
    const code = new Set(previous ? codeLines(previous) : []);
    for (const line of descriptionLines(description))
      line.toggleAttribute("data-new", !lines.has(textWithoutNestedLists(line)));
    for (const pre of description.querySelectorAll("pre")) markNewCodeLines(pre, code);
  });
}
