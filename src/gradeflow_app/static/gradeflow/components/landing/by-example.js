// The marking-by-example demo's timeline (landing/by_example.html).

import { measuredScrollTop, previewChange, swapAndResize } from "./effects.js";
import { markSummaryChanges } from "./new-lines.js";

export function byExampleTimeline(timeline, mockup, labels) {
  const $ = (selector, scope = mockup) => scope.querySelector(selector);
  const $$ = (selector, scope = mockup) => [...scope.querySelectorAll(selector)];
  const headers = $$("[data-answers-header]");
  const lists = $$("[data-answer-list]");
  const summaries = $$("[data-summary]");
  const cards = $(".workspace-cards");
  const answersSection = $("[data-decision-group-indexes]");
  const groupIndexes = answersSection.dataset.decisionGroupIndexes.split(" ");
  const decisions = answersSection.dataset.decisions.split(" ");
  markSummaryChanges(summaries);
  labels.forEach((label, index) => {
    const [list, nextList] = lists.slice(index, index + 2);
    const group = list.children[groupIndexes[index]];
    timeline
      .addLabel(label, index ? "+=1.2" : 0)
      .scroll(cards, { to: measuredScrollTop(timeline, cards, group, true) }, index ? label : 0.6)
      .click($("summary", group))
      .openDetails($("details", group))
      .click($(`[name=decision][value=${decisions[index]}]`, group));
    previewChange(
      timeline,
      [list, nextList],
      summaries.slice(index, index + 2),
      $("[data-marks]", nextList.children[groupIndexes[index]]),
    );
    swapAndResize(timeline, headers.slice(index, index + 2), "<");
    // This group stays at the top as the group decided before closes.
    timeline.scroll(
      cards,
      { to: measuredScrollTop(timeline, cards, nextList.children[groupIndexes[index]], true) },
      "<",
    );
  });
  timeline.set({}, {}, "+=1.4");
}
