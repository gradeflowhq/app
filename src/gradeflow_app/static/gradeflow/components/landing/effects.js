// The demos' timeline effects and helpers, each acting in the mockup (`data-mockup`) around its target.

const EASE = "power2.inOut";
// The answers fade out over their last 3rem (`landing-mockup`).
const FADE_HEIGHT = 48;

const mockupOf = (element) => element.closest("[data-mockup]");
const pointerOf = (element) => mockupOf(element).querySelector("[data-pointer]");

// An element's rectangle in its mockup, in the mockup's own pixels however it's scaled.
function rectInMockup(target) {
  const mockup = mockupOf(target);
  const outer = mockup.getBoundingClientRect();
  const inner = target.getBoundingClientRect();
  const scale = outer.width / mockup.offsetWidth;
  return {
    left: (inner.left - outer.left) / scale,
    top: (inner.top - outer.top) / scale,
    bottom: (inner.bottom - outer.top) / scale,
    height: inner.height / scale,
    width: inner.width / scale,
    x: (inner.left + inner.width / 2 - outer.left) / scale,
    y: (inner.top + inner.height / 2 - outer.top) / scale,
  };
}

// The nearest element around `target` that scrolls.
function scrollerOf(target) {
  for (let element = target.parentElement; element.dataset.mockup === undefined; element = element.parentElement) {
    if (["auto", "scroll"].includes(getComputedStyle(element).overflowY)) return element;
  }
  return null;
}

// The scroll of `scroller` that shows `target` clear of its edges and its fade, or at its top, as far as it scrolls.
const scrollTopToShow = (scroller, target, alignTop) => () => {
  const area = rectInMockup(scroller);
  const item = rectInMockup(target);
  const above = item.top - area.top - 12;
  const below = item.bottom - (area.top + area.height) + FADE_HEIGHT + 12;
  const wanted = scroller.scrollTop + (alignTop || above < 0 ? above : Math.max(below, 0));
  return gsap.utils.clamp(0, scroller.scrollHeight - scroller.clientHeight, wanted);
};

// Text typed into a field a letter at a time, over what it held, its end in view.
export const TypeTextPlugin = {
  name: "typeText",
  init(target, text) {
    Object.assign(this, { target, text, start: target.value });
  },
  render(progress, { target, text, start }) {
    target.value = progress ? text.slice(0, Math.ceil(progress * text.length)) : start;
    target.scrollLeft = target.scrollWidth;
    target.scrollTop = target.scrollHeight;
  },
};

// What `read` finds on the page as the timeline leaves it so far. It rewinds to just before the last label rather than
// to the start: builders add to a timeline only from its last label on.
export function measured(timeline, read) {
  timeline.progress(1, true);
  const value = read();
  timeline.time(Math.max(0, ...Object.values(timeline.labels)) - 1e-4, true);
  return value;
}

// The scroll of `scroller` that shows `target`, or puts it at the top, where the timeline leaves the page: for a scroll
// alongside the motion that takes it there.
export const measuredScrollTop = (timeline, scroller, target, alignTop) =>
  measured(timeline, scrollTopToShow(scroller, target, alignTop));

// One list of answer groups replacing another at `position`: each group of `to` in `pairs` moves and resizes from the
// group of `from` it replaces, by default the one at the same index, and the list's height follows. With
// `scrollAnchor`, a group of `to`, the list's scroller jumps to put it at the top and the groups start from where they
// were on screen, so the anchor stays still. The offsets are measured where the timeline leaves the page. Plain sets and
// tweens, not the Flip plugin, keep seeking exact.
export function flipList(
  timeline,
  [from, to],
  position,
  { pairs = [...to.children].map((group, index) => [from.children[index], group]), scrollAnchor } = {},
) {
  // Each group's top and height in its list, moved as it may be.
  const offsets = (list, groups) => {
    const top = rectInMockup(list).top;
    return groups.map((group) => [rectInMockup(group).top - top, rectInMockup(group).height]);
  };
  const groups = pairs.map(([, group]) => group);
  const scroller = scrollerOf(to);
  const [before, scrollTop] = measured(timeline, () => [
    offsets(
      from,
      pairs.map(([group]) => group),
    ),
    scroller?.scrollTop,
  ]);
  const fromHeight = Math.max(...before.map(([top, height]) => top + height));
  timeline.swap([from, to], position);
  // A scroll alongside the move would shake: scrolls snap to whole pixels in Safari.
  let scrollDelta = 0;
  if (scrollAnchor) {
    const anchorScrollTop = measuredScrollTop(timeline, scroller, scrollAnchor, true);
    scrollDelta = anchorScrollTop - scrollTop;
    timeline.set(scroller, { scrollTop: anchorScrollTop }, "<");
  }
  const [after, toHeight] = measured(timeline, () => [offsets(to, groups), to.offsetHeight]);
  const moveTween = { duration: 0.55, ease: EASE, immediateRender: false };
  // Out of the flow while they move, whichever way the timeline reaches them.
  const outOfFlow = { position: "absolute", left: 0, right: 0 };
  // The list keeps its height while its groups are out of the flow: a shorter list would clamp its scroller's scroll.
  timeline
    .fromTo(to, { height: fromHeight + scrollDelta }, { height: toHeight, ...moveTween, clearProps: "height" }, "<")
    .fromTo(
      groups,
      { ...outOfFlow, top: (i) => before[i][0] + scrollDelta, height: (i) => before[i][1] },
      {
        ...outOfFlow,
        top: (i) => after[i][0],
        height: (i) => after[i][1],
        ...moveTween,
        clearProps: "position,left,right,top,height",
      },
      "<",
    );
}

// One block replacing another at `position`, its height growing or shrinking from the first's.
export function swapAndResize(timeline, [from, to], position) {
  const fromHeight = measured(timeline, () => from.offsetHeight);
  timeline.swap([from, to], position);
  const toHeight = measured(timeline, () => to.offsetHeight);
  timeline.fromTo(
    to,
    { height: fromHeight, overflow: "hidden" },
    {
      height: toHeight,
      overflow: "hidden",
      duration: 0.55,
      ease: EASE,
      immediateRender: false,
      clearProps: "height,overflow",
    },
    "<",
  );
}

// A change to a marking previewed at `position`: the next answers and summary replace the current ones, the group
// `scrollAnchor` held at the top as in `flipList`, the marks `changed` pop in, the summary's text fades in and its new
// lines (`data-new`) rise in.
export function previewChange(timeline, [list, nextList], summaries, changed, position, scrollAnchor) {
  flipList(timeline, [list, nextList], position, { scrollAnchor });
  swapAndResize(timeline, summaries, "<");
  timeline
    .from(changed, { autoAlpha: 0, scale: 0.6, duration: 0.35, ease: "back.out(3)", immediateRender: false }, "<")
    .from(summaries[1].querySelector(".prose-core"), { autoAlpha: 0, duration: 0.45, immediateRender: false }, "<");
  const newLines = summaries[1].querySelectorAll("[data-new]");
  if (newLines.length) {
    timeline.from(newLines, { opacity: 0, y: 4, duration: 0.4, ease: "power1.out", immediateRender: false }, "<");
  }
}

export function registerEffects() {
  const effect = (name, make, defaults = {}) =>
    gsap.registerEffect({ name, effect: make, defaults, extendTimeline: true });

  effect("scroll", ([scroller], { to }) =>
    gsap.to(scroller, { scrollTop: to, duration: 0.5, ease: EASE, immediateRender: false }),
  );

  // The pointer curves over to the target as it scrolls into view, pauses, and clicks: a ripple, and the target dips.
  effect(
    "click",
    ([target], { duration }) => {
      const pointer = pointerOf(target);
      const scroller = scrollerOf(target);
      const timeline = gsap.timeline();
      // Where the target ends up is where it is plus the scroll still to come, whenever that's read.
      let scrollTop = 0;
      if (scroller) {
        const targetScrollTop = scrollTopToShow(scroller, target);
        timeline.to(scroller, {
          scrollTop: () => {
            scrollTop = targetScrollTop();
            return scrollTop;
          },
          duration,
          ease: EASE,
          immediateRender: false,
        });
      }
      const remainingScroll = () => (scroller ? scroller.scrollTop - scrollTop : 0);
      return timeline
        .to(pointer, { x: () => rectInMockup(target).x, duration, ease: EASE }, 0)
        .to(pointer, { y: () => rectInMockup(target).y + remainingScroll(), duration, ease: "sine.inOut" }, 0)
        .fromTo(
          pointer.lastElementChild,
          { scale: 0.3, opacity: 0.8 },
          { scale: 1.6, opacity: 0, duration: 0.35, immediateRender: false },
          "+=0.08",
        )
        .to(target, { keyframes: { scale: [1, 0.96, 1] }, duration: 0.25 }, "<")
        .set({}, {}, "+=0.05");
    },
    { duration: 0.45 },
  );

  // The pointer carries `item` onto the target.
  effect("drag", ([item], { to }) =>
    gsap
      .timeline()
      .to([item, pointerOf(item)], { x: () => rectInMockup(to).x, duration: 1.2, ease: EASE })
      .to([item, pointerOf(item)], { y: () => rectInMockup(to).y, duration: 1.2, ease: "sine.inOut" }, "<"),
  );

  // The field takes focus, the pointer moves out of the way, and the text appears a letter at a time.
  effect("type", ([field], { text }) => {
    const fieldBox = field.closest(".input, .textarea") ?? field;
    const bottom = () => mockupOf(field).offsetHeight - 16;
    return gsap
      .timeline()
      .click(field)
      .set(fieldBox, { attr: { "data-focus": 1 } })
      .to(pointerOf(field), {
        x: () => rectInMockup(fieldBox).left + 24,
        y: () => Math.min(rectInMockup(fieldBox).bottom + 14, bottom()),
        duration: 0.3,
        ease: "power2.out",
      })
      .to(field, { typeText: text, duration: text.length / 42, ease: "none" }, "<")
      .set(fieldBox, { attr: { "data-focus": 0 } }, "+=0.2");
  });

  // An answer group opens, its body growing from nothing.
  effect("openDetails", ([details]) =>
    gsap
      .timeline()
      .set(details, { open: true })
      .fromTo(
        details.querySelector(":scope > div"),
        { height: 0, overflow: "hidden" },
        { height: "auto", duration: 0.4, ease: EASE, immediateRender: false },
      ),
  );

  // Parts of the page a toggle shows grow from nothing.
  effect("expand", (targets) =>
    gsap.fromTo(
      targets,
      { height: 0, autoAlpha: 0, overflow: "hidden" },
      { height: "auto", autoAlpha: 1, duration: 0.4, ease: EASE, immediateRender: false },
    ),
  );

  // Something appears, fading in, or goes, or one thing replaces another.
  effect(
    "show",
    (targets, { y }) =>
      gsap
        .timeline()
        .set(targets, { attr: { "data-shown": 1 } })
        .fromTo(targets, { autoAlpha: 0, y }, { autoAlpha: 1, y: 0, duration: 0.3, immediateRender: false }),
    { y: 6 },
  );
  effect("hide", (targets) => gsap.timeline().set(targets, { attr: { "data-shown": 0 } }));
  effect("swap", ([from, to]) =>
    gsap
      .timeline()
      .set(from, { attr: { "data-shown": 0 } })
      .set(to, { attr: { "data-shown": 1 } }),
  );

  // Another page: its tab is the current one and the page rises in.
  effect("switchPage", ([tab]) => {
    const mockup = mockupOf(tab);
    const page = mockup.querySelector(`[data-page="${tab.dataset.tab}"]`);
    return gsap
      .timeline()
      .set(mockup.querySelectorAll("[data-tab]"), { attr: { "aria-current": "false" } })
      .set(tab, { attr: { "aria-current": "page" } })
      .to(mockup.querySelectorAll("[data-page]"), { autoAlpha: 0, duration: 0.2 })
      .fromTo(page, { autoAlpha: 0, y: 10 }, { autoAlpha: 1, y: 0, duration: 0.4, immediateRender: false });
  });

  // A select opens its list (`menu`) under it, and the option `value` is selected.
  effect("selectOption", ([select], { menu, value }) =>
    gsap
      .timeline()
      .click(select)
      .set(menu, {
        left: () => rectInMockup(select).left,
        top: () => rectInMockup(select).bottom + 4,
        minWidth: () => rectInMockup(select).width,
      })
      .fromTo(menu, { autoAlpha: 0, y: -4 }, { autoAlpha: 1, y: 0, duration: 0.2, immediateRender: false })
      .click(menu.querySelector(`[data-value="${value}"]`), { duration: 0.4 })
      .to(menu, { autoAlpha: 0, y: -4, duration: 0.2 })
      .set(select, { value }, "<"),
  );

  // The method picker opens from `button`, and the method `label` is selected and used.
  effect("pickMethod", ([button], { label }) => {
    const picker = mockupOf(button).querySelector("[data-picker]");
    const item = picker.querySelector(`[data-label="${label}"]`);
    const selectMethod = (method) =>
      gsap
        .timeline()
        .set(picker.querySelectorAll("[data-label]"), { attr: { "aria-current": "false" } })
        .set(method, { attr: { "aria-current": "true" } })
        .set(picker.querySelectorAll("[data-use-method]"), { attr: { "data-shown": 0 } })
        .set(picker.querySelector(`[data-use-method="${method.dataset.label}"]`), { attr: { "data-shown": 1 } });
    return gsap
      .timeline()
      .click(button)
      .add(selectMethod(picker.querySelector("[data-label]")))
      .openDialog(picker)
      .click(item)
      .add(selectMethod(item))
      .click(picker.querySelector("[data-use-button]"), { duration: 0.4 })
      .to(picker, { autoAlpha: 0, duration: 0.2 });
  });

  // A dialog over the mockup (`landing-hero-backdrop`) fades in and rises.
  effect("openDialog", ([backdrop]) =>
    gsap
      .timeline()
      .fromTo(backdrop, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.2, immediateRender: false })
      .fromTo(
        backdrop.firstElementChild,
        { y: 10 },
        { y: 0, duration: 0.25, ease: "power2.out", immediateRender: false },
        "<",
      ),
  );

  effect("countUp", ([target]) =>
    gsap.fromTo(
      target,
      { innerText: 0 },
      {
        innerText: Number.parseFloat(target.textContent),
        snap: { innerText: 1 },
        duration: 0.8,
        ease: "power1.out",
        immediateRender: false,
      },
    ),
  );

  effect("showToast", ([toast]) =>
    gsap
      .timeline()
      .fromTo(toast, { autoAlpha: 0, y: 12 }, { autoAlpha: 1, y: 0, duration: 0.3, immediateRender: false })
      .to(toast, { autoAlpha: 0, y: 12, duration: 0.3 }, "+=2.2"),
  );

  // Answer groups move into the group they join (`targetIndexes`, indexes into `cards`), shrinking and fading, while
  // the groups below them move up to close the gap.
  effect("mergeGroups", (cards, { targetIndexes }) => {
    const isMerging = (index) => targetIndexes[index] !== index;
    const gap = (index) => cards[index + 1].offsetTop - cards[index].offsetTop;
    const shiftUp = (index) =>
      cards.slice(0, index).reduce((sum, _, above) => sum + (isMerging(above) ? gap(above) : 0), 0);
    const timeline = gsap.timeline({ defaults: { duration: 0.6, ease: EASE } });
    cards.forEach((card, index) => {
      const target = targetIndexes[index];
      timeline.to(
        card,
        isMerging(index)
          ? { y: () => cards[target].offsetTop - shiftUp(target) - card.offsetTop, autoAlpha: 0, scale: 0.94 }
          : { y: () => -shiftUp(index) },
        isMerging(index) ? index * 0.04 : 0,
      );
    });
    return timeline;
  });
}

// The marks and feedback of an answer group set by hand at `position`, as the answers section's
// `data-adjusted-group-index`, `data-adjusted-marks` and `data-adjusted-feedback` say: the group scrolls to the top and
// opens, Adjust marks is pressed, and the form, scrolled into view, is filled in and applied. Back at the top, the
// group's body fades and `adjustedList` replaces the list.
export function adjustMarks(timeline, [list, adjustedList], position) {
  const { adjustedGroupIndex, adjustedMarks, adjustedFeedback } = list.closest("[data-adjusted-group-index]").dataset;
  const group = list.children[adjustedGroupIndex];
  const scroller = list.closest(".workspace-cards");
  const details = group.querySelector("details");
  const toggle = group.querySelector("[data-adjust]");
  const form = group.querySelector("form.shaded-box");
  const groupAtTop = measuredScrollTop(timeline, scroller, group, true);
  timeline
    .scroll(scroller, { to: groupAtTop }, position)
    .click(details.querySelector("summary"))
    .openDetails(details)
    .click(toggle.closest("label"))
    .set(toggle, { checked: true })
    .expand(details.querySelectorAll('[aria-label="Answers"], form.shaded-box'));
  timeline
    .scroll(scroller, { to: measuredScrollTop(timeline, scroller, form) }, "<")
    .type(form.querySelector("[name=marks]"), { text: adjustedMarks })
    .type(form.querySelector("[name=feedback]"), { text: adjustedFeedback })
    .click(form.querySelector("[name=action][value=apply]"))
    .scroll(scroller, { to: groupAtTop })
    .to(details.querySelector(":scope > div"), { autoAlpha: 0, duration: 0.3 }, "<");
  flipList(timeline, [list, adjustedList]);
}
