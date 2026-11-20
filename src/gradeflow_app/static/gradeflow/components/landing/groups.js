// The grouping demo's timeline (landing/groups.html).

import { adjustMarks, flipList } from "./effects.js";

export function groupsTimeline(timeline, mockup, [same, spelling, meaning, adjusting]) {
  const $ = (selector, scope = mockup) => scope.querySelector(selector);
  const $$ = (selector, scope = mockup) => [...scope.querySelectorAll(selector)];
  const [sameHeader, spellingHeader, meaningHeader] = $$("[data-answers-header]");
  const [sameList, spellingList, meaningList, adjustedList] = $$("[data-answer-list]");
  const menu = $('[data-menu="group"]');

  // Each group of `list` moves into the first of those joining the same group of `nextList`, which then replaces it.
  const regroup = (label, header, nextHeader, list, nextList, value) => {
    const groups = [...list.children];
    const nextGroupIndexes = list.dataset.nextGroupIndexes.split(" ").map(Number);
    const mergeTargetIndexes = nextGroupIndexes.map((nextIndex) => nextGroupIndexes.indexOf(nextIndex));
    timeline
      .addLabel(label, "+=1")
      .selectOption($("select[name=group]", header), { menu, value }, label)
      .swap([header, nextHeader])
      .mergeGroups(groups, { targetIndexes: mergeTargetIndexes }, "+=0.2");
    flipList(timeline, [list, nextList], "+=0", {
      pairs: [...nextList.children].map((group, index) => [groups[nextGroupIndexes.indexOf(index)], group]),
    });
  };

  timeline.addLabel(same).set({}, {}, "+=0.6");
  regroup(spelling, sameHeader, spellingHeader, sameList, spellingList, "spelling");
  regroup(meaning, spellingHeader, meaningHeader, spellingList, meaningList, "meaning");
  timeline.addLabel(adjusting, "+=1");
  adjustMarks(timeline, [meaningList, adjustedList], adjusting);
  timeline.showToast($('[data-toast="adjusted"]'), {}, "<").set({}, {}, "+=0.6");
}
