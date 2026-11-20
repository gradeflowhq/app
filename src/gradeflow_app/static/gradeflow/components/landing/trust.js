// The trust demo's timeline (landing/trust.html).

import { previewChange } from "./effects.js";
import { markSummaryChanges } from "./new-lines.js";

export function trustTimeline(timeline, mockup, [sameMark, breakdown, ai]) {
  const $ = (selector, scope = mockup) => scope.querySelector(selector);
  const $$ = (selector, scope = mockup) => [...scope.querySelectorAll(selector)];
  const pane = (name) => $(`[data-pane="${name}"]`);

  // The made-up students' IDs aren't encrypted.
  for (const id of $$("[data-student-id]")) {
    id.textContent = id.dataset.studentId;
    id.dataset.studentIdState = "shown";
  }

  const sameMarkGroup = $("[data-answer-list] > li", pane("same-mark"));
  const studentsToggle = $("[data-students]", sameMarkGroup);
  timeline
    .addLabel(sameMark)
    .click($("summary", sameMarkGroup), {}, 0.6)
    .openDetails($("details", sameMarkGroup))
    .click(studentsToggle.closest("label"))
    .set(studentsToggle, { checked: true })
    .expand($('[aria-label="Students"]', sameMarkGroup))
    .set({}, {}, "+=2.2");

  const breakdownPane = pane("breakdown");
  const openedGroup = $("[data-answer-list]", breakdownPane).children[breakdownPane.dataset.openedGroupIndex];
  const openedDetails = $("details", openedGroup);
  timeline
    .addLabel(breakdown)
    .hide(pane("same-mark"), breakdown)
    .show(breakdownPane, { y: 0 })
    .click($("summary", openedGroup), {}, "+=0.5")
    .openDetails(openedDetails)
    .from(
      $$(".bordered-list header, .bordered-list li", openedDetails),
      { autoAlpha: 0, x: -6, stagger: 0.35, duration: 0.3, immediateRender: false },
      "+=0.1",
    )
    .set({}, {}, "+=2.2");

  const aiPane = pane("ai");
  const lists = $$("[data-answer-list]", aiPane);
  const summaries = $$("[data-summary]", aiPane);
  markSummaryChanges(summaries);
  timeline.addLabel(ai).hide(breakdownPane, ai).show(aiPane, { y: 0 });
  previewChange(timeline, lists, summaries, $$("[data-marks]:has(> .line-through)", lists[1]), "+=1.8");
  timeline.set({}, {}, "+=3");
}
