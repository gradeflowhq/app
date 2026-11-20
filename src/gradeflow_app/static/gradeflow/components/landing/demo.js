// The landing page's demos: GSAP timelines that switch between states the server renders in a mockup of the app.

import { loadScript } from "../../services/vendor.js";
import { byExampleTimeline } from "./by-example.js";
import { registerEffects, TypeTextPlugin } from "./effects.js";
import { groupsTimeline } from "./groups.js";
import { heroTimeline } from "./hero.js";
import { methodTimeline } from "./methods.js";
import { trustTimeline } from "./trust.js";

const TIMELINES = {
  hero: heroTimeline,
  trust: trustTimeline,
  groups: groupsTimeline,
  "by-example": byExampleTimeline,
  method: methodTimeline,
};
// The timelines are rebuilt when a breakpoint changes the layout.
const CONDITIONS = {
  motion: "(prefers-reduced-motion: no-preference)",
  reducedMotion: "(prefers-reduced-motion: reduce)",
  lg: "(min-width: 1024px)",
  md: "(min-width: 768px)",
};
const REPEAT_DELAY = 2;
const SCROLL_MARGIN = 16;

// GSAP, its plugins and the demos' effects, once the page's intro animation has finished or stopped: building a demo
// blocks the main thread for a moment.
async function loadGsap() {
  await loadScript("gsap-3.13.0/gsap.min.js");
  await Promise.all([loadScript("gsap-3.13.0/ScrollTrigger.min.js"), loadScript("gsap-3.13.0/ScrollToPlugin.min.js")]);
  const introAnimations = document.querySelector(".landing-hero-intro").getAnimations({ subtree: true });
  await Promise.allSettled(introAnimations.map((animation) => animation.finished));
  gsap.registerPlugin(ScrollTrigger, ScrollToPlugin, TypeTextPlugin);
  // Tweens write when they first render rather than at the end of the tick, as tweens after them measure the page.
  gsap.defaults({ lazy: false });
  registerEffects();
}

let gsapLoaded;
// Loads GSAP once for every demo, then yields to the event loop: each demo builds in a task of its own.
async function loadGsapOnce() {
  gsapLoaded ??= loadGsap();
  await gsapLoaded;
  await new Promise((resolve) => setTimeout(resolve));
}

export const landingDemo = () => {
  let media;
  return {
    async init() {
      await loadGsapOnce();
      media = gsap.matchMedia();
      media.add(CONDITIONS, ({ conditions }) => play(this.$root, conditions.reducedMotion));
    },
    destroy() {
      media?.revert();
    },
  };
};

// The method demos (landing/methods.html), one for each method's tab: the chosen method's plays while in view, then the
// next method's, after the last the first. Choosing a method plays its demo from the start. Pausing stops it, and
// playing a chapter from the chapter bar starts it again. A demo is built when it first shows on screen. With reduced
// motion, the chosen demo shows its end.
export const methodDemos = () => {
  let media;
  let actions;
  return {
    playing: true,
    async init() {
      await loadGsapOnce();
      media = gsap.matchMedia();
      media.add(CONDITIONS, (context) => {
        const { reducedMotion } = context.conditions;
        const roots = [...this.$root.querySelectorAll("[data-demo]")];
        const builtDemos = new Map();
        let shownRoot = roots.find((root) => !root.hidden);
        let inView = false;
        const sync = () => {
          const { timeline } = builtDemos.get(shownRoot);
          if (inView && this.playing) timeline.play();
          else timeline.pause();
        };
        const show = (root) => {
          for (const other of roots) other.hidden = other !== root;
          builtDemos.get(shownRoot)?.timeline.pause();
          shownRoot = root;
          if (!builtDemos.has(root)) {
            const demo = context.add(() =>
              build(root, reducedMotion, () => {
                this.playing = true;
              }),
            );
            if (!reducedMotion) {
              demo.timeline.eventCallback("onComplete", () => {
                const next = roots[(roots.indexOf(shownRoot) + 1) % roots.length];
                const choice = this.$root.querySelector(`[name=example][value="${next.dataset.example}"]`);
                choice.checked = true;
                scrollToTab(choice.closest("label"));
                show(next);
              });
            }
            builtDemos.set(root, demo);
          } else if (!reducedMotion) {
            builtDemos.get(root).timeline.pause(0);
          }
          if (!reducedMotion) sync();
        };
        // The first demo is built as the section comes into view, not with the page.
        ScrollTrigger.create({
          trigger: this.$root,
          start: "top bottom",
          end: "bottom top",
          onToggle: ({ isActive }) => {
            inView = isActive;
            if (!builtDemos.has(shownRoot)) show(shownRoot);
            else if (!reducedMotion) sync();
          },
        });
        actions = {
          choose: (example) => show(roots.find((root) => root.dataset.example === example)),
          togglePlaying: () => {
            this.playing = !this.playing;
            sync();
          },
        };
        return () => {
          for (const demo of builtDemos.values()) demo.stop();
        };
      });
    },
    choose(event) {
      actions?.choose(event.target.value);
    },
    togglePlaying() {
      actions?.togglePlaying();
    },
    destroy() {
      media?.revert();
    },
  };
};

// Scrolls the row of tabs that holds `tab` to show it in the middle, leaving the page where it is.
function scrollToTab(tab) {
  const row = tab.parentElement;
  const offset = tab.getBoundingClientRect().left - row.getBoundingClientRect().left;
  row.scrollTo({ left: row.scrollLeft + offset - (row.clientWidth - tab.offsetWidth) / 2, behavior: "smooth" });
}

// Builds the demo at `root`, paused, or with reduced motion at its end. `onPlay` is called when the chapter bar plays
// it.
function build(root, reducedMotion, onPlay) {
  const mockup = root.querySelector("[data-mockup]");
  const width = Number(mockup.dataset.width);
  const resizeObserver = new ResizeObserver(([entry]) => gsap.set(mockup, { scale: entry.contentRect.width / width }));
  if (width) resizeObserver.observe(mockup.parentElement);
  const pointer = mockup.querySelector("[data-pointer]");
  if (pointer) gsap.set(pointer, { autoAlpha: 1, x: mockup.offsetWidth - 140, y: mockup.offsetHeight - 30 });
  const chapters = [...root.querySelectorAll("[data-chapter]")];
  const timeline = gsap.timeline({ paused: true, defaults: { duration: 0.35, ease: "power2.out" } });
  TIMELINES[root.dataset.demo](
    timeline,
    mockup,
    chapters.map((chapter) => chapter.dataset.chapter),
  );
  // measured() leaves the timeline just before its last label, and the chapter bar adds to it from the start.
  timeline.progress(0, true);
  const starts = fillChapters(chapters, timeline);
  if (reducedMotion) {
    gsap.set(mockup.querySelectorAll("[data-pointer], [data-dragged-file]"), { autoAlpha: 0 });
    timeline.progress(1);
  }
  bindChapterBar(root, chapters, timeline, starts, reducedMotion, onPlay);
  return {
    timeline,
    stop() {
      resizeObserver.disconnect();
    },
  };
}

// Plays the demo at `root` while it's on screen, over and over.
function play(root, reducedMotion) {
  const { timeline, stop } = build(root, reducedMotion, () => {});
  if (!reducedMotion) {
    timeline.repeat(-1).repeatDelay(REPEAT_DELAY);
    ScrollTrigger.create({
      trigger: root.querySelector("[data-mockup]"),
      animation: timeline,
      start: "top bottom",
      end: "bottom top",
      toggleActions: "play pause resume pause",
    });
  }
  const unbindDemoLinks = bindDemoLinks(root, timeline, reducedMotion);
  return () => {
    stop();
    unbindDemoLinks();
  };
}

// Links to the demo smooth-scroll it into view under the site's header, whole if it fits or else from its top, and
// play it from the start. With reduced motion they jump there.
function bindDemoLinks(root, timeline, reducedMotion) {
  const anchors = root.id ? document.querySelectorAll(`a[href="#${root.id}"]`) : [];
  const scrollTarget = () => {
    const headerHeight = document.querySelector("body > header").offsetHeight;
    const { height } = root.getBoundingClientRect();
    const fits = headerHeight + height + 2 * SCROLL_MARGIN <= innerHeight;
    return { y: root, offsetY: fits ? innerHeight - height - SCROLL_MARGIN : headerHeight + SCROLL_MARGIN };
  };
  const scrollToDemo = (event) => {
    event.preventDefault();
    if (reducedMotion) {
      gsap.set(window, { scrollTo: scrollTarget() });
      return;
    }
    gsap.to(window, {
      scrollTo: scrollTarget(),
      duration: 1.3,
      ease: "power2.inOut",
      onComplete: () => timeline.restart(),
    });
  };
  for (const anchor of anchors) anchor.addEventListener("click", scrollToDemo);
  return () => {
    for (const anchor of anchors) anchor.removeEventListener("click", scrollToDemo);
  };
}

// The chapter bar fills with the timeline: each chapter's track over that chapter's part of it.
function fillChapters(chapters, timeline) {
  const starts = [...chapters.map((chapter) => timeline.labels[chapter.dataset.chapter]), timeline.duration()];
  chapters.forEach((chapter, index) => {
    timeline
      .set(chapter, { attr: { "aria-current": "true" } }, starts[index])
      .fromTo(
        chapter.querySelector(".landing-chapters-track > span"),
        { scaleX: 0 },
        { scaleX: 1, duration: starts[index + 1] - starts[index], ease: "none", immediateRender: false },
        starts[index],
      )
      .set(chapter, { attr: { "aria-current": "false" } }, starts[index + 1]);
  });
  return starts;
}

// Clicking a chapter plays from it, or with reduced motion shows its end. Dragging along a chapter bar scrubs; a list of
// claims (landing/trust.html) is only clicked. `onPlay` is called each time it plays.
function bindChapterBar(root, chapters, timeline, starts, reducedMotion, onPlay) {
  const bar = root.querySelector(".landing-chapters, .landing-claims");
  const scrubs = bar.matches(".landing-chapters");
  // Each chapter takes an equal share of the bar. The bar's end stays inside the last chapter.
  const timeAt = (clientX) => {
    const { left, width } = bar.getBoundingClientRect();
    return gsap.utils.interpolate(starts, gsap.utils.clamp(0, 0.999, (clientX - left) / width));
  };
  let dragged = false;
  ScrollTrigger.observe({
    target: bar,
    type: "pointer",
    dragMinimum: 5,
    debounce: false,
    onPress: () => {
      dragged = false;
    },
    onDrag: ({ x }) => {
      if (!scrubs) return;
      dragged = true;
      timeline.pause(timeAt(x));
    },
    onDragEnd: () => {
      if (reducedMotion || !dragged) return;
      timeline.play();
      onPlay();
    },
    onClick: ({ event }) => {
      const index = chapters.indexOf(event.target.closest("[data-chapter]"));
      if (dragged || index < 0) return;
      if (reducedMotion) {
        timeline.pause(starts[index + 1] - 0.001);
        return;
      }
      timeline.play(starts[index]);
      onPlay();
    },
  });
}
