// A method demo's timeline (landing/methods.html).

import { previewChange } from "./effects.js";
import { markSummaryChanges } from "./new-lines.js";

const FIRST_CHAPTER_SECONDS = 2.5;
const CHAPTER_SECONDS = 3;

export function methodTimeline(timeline, mockup, labels) {
  const $$ = (selector) => [...mockup.querySelectorAll(selector)];
  const lists = $$("[data-answer-list]");
  const summaries = $$("[data-summary]");
  markSummaryChanges(summaries);
  timeline.addLabel(labels[0], 0);
  labels.slice(1).forEach((label, index) => {
    timeline.addLabel(label, `+=${index ? CHAPTER_SECONDS : FIRST_CHAPTER_SECONDS}`);
    previewChange(
      timeline,
      lists.slice(index, index + 2),
      summaries.slice(index, index + 2),
      lists[index + 1].querySelectorAll("[data-marks]:has(> .line-through)"),
      // A label read as a position would take "=" in it for an offset.
      timeline.labels[label],
    );
  });
  timeline.set({}, {}, `+=${CHAPTER_SECONDS}`);
}
