// The hero demo's timeline (landing/hero.html).

import { adjustMarks, flipList, measured, swapAndResize } from "./effects.js";

// What a step opening or closing shows fades in.
const fadeIn = (targets) =>
  gsap.fromTo(targets, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.3, immediateRender: false });

export function heroTimeline(timeline, mockup, [importing, writing, marking, adjusting, students, sending]) {
  const $ = (selector, scope = mockup) => scope.querySelector(selector);
  const $$ = (selector, scope = mockup) => [...scope.querySelectorAll(selector)];
  const tab = (name) => $(`[data-tab="${name}"]`);

  const file = $("[data-dragged-file]");
  const dropZone = $("[data-drop-zone]");
  const imported = $("[data-imported]");
  gsap.set(file, {
    autoAlpha: 1,
    xPercent: -50,
    yPercent: -50,
    x: mockup.offsetWidth - 140,
    y: mockup.offsetHeight - 30,
  });
  timeline
    .addLabel(importing)
    .drag(file, { to: dropZone }, 0.8)
    .set(dropZone, { attr: { class: `${dropZone.className} border-primary bg-base-200` } }, "-=0.35")
    .to(file, { autoAlpha: 0, scale: 0.9, duration: 0.25 }, "+=0.2")
    .swap([dropZone, $("[data-reading]")])
    .hide($("[data-import]"), "+=1")
    .set(imported, { attr: { "data-shown": 1 } })
    .fromTo(imported.children, { autoAlpha: 0, y: 8 }, { autoAlpha: 1, y: 0, immediateRender: false })
    .countUp($(".figures strong"), {}, "<")
    .show($$("[data-tab] .chip"), { y: 0 }, "<");

  const methodChoices = $("[data-method-choices]");
  const advanced = $("details", methodChoices);
  timeline
    .addLabel(writing, "+=0.8")
    .click($("[data-go-to-question]"), {}, writing)
    .switchPage(tab("questions"))
    .click($("summary", advanced))
    .openDetails(advanced)
    .click($(".method-choice", advanced))
    .swap([methodChoices, $("[data-marking-editor]")])
    .add(fadeIn($("[data-marking-editor]")));
  const stages = $$("[data-stage]");
  // Each stage is an editor panel with its own scrolling body; the next stage opens at the same scroll.
  const body = (stage) => $(".workspace-panel > div", stage);
  stages.forEach((stage, index) => {
    const { editAction: action, editFieldName: name, editValue: value } = stage.dataset;
    if (!action) return;
    const field = $(`[name="${name}"]`, stage);
    switch (action) {
      case "add_tag":
        timeline.type($(`[data-tag-input="${name}"] input`, stage), { text: value });
        break;
      case "type":
        timeline.type(field, { text: value });
        break;
      case "select":
        if (field.tagName === "SELECT") timeline.selectOption(field, { menu: $(`[data-menu="${index}"]`), value });
        else timeline.click($(`[name="${name}"][value="${value}"]`, stage).closest("label"));
        break;
      case "add_row":
        timeline.click($(`[hx-vals*='"target": "${name}"']`, stage));
        break;
      case "add_step":
        timeline.pickMethod($('[hx-get*="operation=add&"]', stage), { label: value });
        break;
      case "close_step":
        timeline.click($('nav[aria-label="Where you are"] button', stage));
        break;
    }
    const scrollTop = action === "close_step" ? 0 : measured(timeline, () => body(stage).scrollTop);
    timeline.swap([stage, stages[index + 1]], "+=0.1").set(body(stages[index + 1]), { scrollTop }, "<");
    // A step opening or closing replaces the whole form.
    if (action === "add_step" || action === "close_step") timeline.add(fadeIn(body(stages[index + 1])), "<");
  });
  timeline
    .click($(".panel-foot .btn-primary", stages.at(-1)), {}, "+=0.2")
    .swap([$("[data-marking-editor]"), $("[data-saved-marking]")])
    .add(fadeIn($("[data-saved-marking]")));

  // Once saved, Q1 is marked, then the other questions one after another.
  const [idleHeader, markingHeader] = $$("[data-answers-header]");
  const [unmarked, marked, adjusted] = $$("[data-answer-list]");
  const [notMarked, markingList, allMarked, allAdjusted] = $$("[data-question-list]");
  const [, ...othersMarking] = markingList.children;
  const [, ...othersMarked] = allMarked.children;
  const markedCount = $("[data-marked-count]");
  timeline.addLabel(marking);
  swapAndResize(timeline, [idleHeader, markingHeader], marking);
  timeline
    .swap([notMarked, markingList], "<")
    .to($$("[data-marks]", unmarked), { opacity: 0.4, duration: 0.2 }, "<")
    .to([...$$("progress", markingHeader), ...$$("progress", markingList)], {
      attr: { value: 1 },
      duration: 1.6,
      ease: "power1.inOut",
    })
    .to(
      $(".preview-bar .tabular-nums", markingHeader),
      { innerText: "100%", snap: { innerText: 1 }, duration: 1.6 },
      "<",
    )
    .addLabel("marked");
  swapAndResize(timeline, [markingHeader, idleHeader], "marked");
  flipList(timeline, [unmarked, marked], "<");
  timeline
    .from(
      $$("[data-marks]", marked),
      { autoAlpha: 0, scale: 0.4, stagger: 0.08, ease: "back.out(3)", immediateRender: false },
      "<",
    )
    .set(allMarked, { attr: { "data-shown": 1 } }, "<")
    .set(othersMarked, { autoAlpha: 0 }, "<")
    .set(markingList.children[0], { autoAlpha: 0 }, "<")
    .set(markedCount, { innerText: 1 }, "<")
    .set(othersMarked, { autoAlpha: 1, stagger: 0.1 }, "+=0.5")
    .set(othersMarking, { autoAlpha: 0, stagger: 0.1 }, "<")
    .to(markedCount, { innerText: allMarked.children.length, snap: { innerText: 1 }, duration: 0.7, ease: "none" }, "<")
    .hide(markingList);

  timeline.addLabel(adjusting, "+=0.6");
  adjustMarks(timeline, [marked, adjusted], adjusting);
  timeline.swap([allMarked, allAdjusted], "<").showToast($('[data-toast="adjusted"]'), {}, "<");

  const rows = $$("[data-page=students] tbody tr");
  // The app's rows have no data-shown: `hide` sets it to 0, and a rewind restores the value it replaced.
  for (const row of rows) row.dataset.shown = "1";
  const isAdjusted = (row) => row.querySelector("[data-adjusted]") !== null;
  const unadjustedRows = rows.filter((row) => !isAdjusted(row));
  const adjustedFilter = $("[data-page=students] input[value=adjusted]").closest("label");
  timeline
    .addLabel(students, "+=0.3")
    .click(tab("students"), {}, students)
    .switchPage(tab("students"))
    .from(rows, { autoAlpha: 0, y: 8, stagger: 0.03, immediateRender: false }, "<0.2")
    .click(adjustedFilter, {}, "+=0.5")
    .set($("input", adjustedFilter), { checked: true });
  const adjustedRows = rows.filter(isAdjusted);
  const rowTopsBefore = measured(timeline, () => adjustedRows.map((row) => row.offsetTop));
  timeline.to(unadjustedRows, { autoAlpha: 0, duration: 0.25 }).hide(unadjustedRows);
  const rowTopsAfter = measured(timeline, () => adjustedRows.map((row) => row.offsetTop));
  timeline.fromTo(
    adjustedRows,
    { y: (i) => rowTopsBefore[i] - rowTopsAfter[i] },
    { y: 0, duration: 0.5, ease: "power2.inOut", immediateRender: false },
    "<",
  );

  // Canvas updates the grades after the review closes; then they're up to date and the send is listed.
  const sendReview = $("[data-send-review]");
  const confirmSend = $("[data-confirm-send]");
  const [confirmLabel, confirmSpinner] = confirmSend.children;
  const sendGrades = $("[data-send-grades]");
  const sendingGrades = $("[data-sending-grades]");
  const updating = $("[data-updating]");
  timeline
    .addLabel(sending, "+=1")
    .click(tab("export"), {}, sending)
    .switchPage(tab("export"))
    .click(sendGrades, {}, "+=0.4")
    .openDialog(sendReview)
    .click(confirmSend, {}, "+=0.3")
    .to(confirmLabel, { autoAlpha: 0, duration: 0 })
    .to(confirmSpinner, { attr: { "data-shown": 1 }, duration: 0 }, "<")
    .to(sendReview, { autoAlpha: 0, duration: 0.2 }, "+=0.5")
    .swap([sendGrades, sendingGrades], "<")
    .show(updating, {}, "<")
    .hide(updating, "+=1.6")
    .swap([sendingGrades, $("[data-up-to-date]")], "<")
    .swap([$("[data-students-to-send]"), $("[data-students-sent]")], "<")
    .hide($("[data-filter-to-send]"), "<")
    .show($("[data-sent-to-canvas]"), {}, "<")
    .showToast($('[data-toast="sent"]'), {}, "<")
    .set({}, {}, "+=0.6");
}
