// The landing page's demos: the workspace and review scenes, and the method slideshow.

const PRESS_MS = 450;
const FIRST_VARIANT_MS = 2500;
const VARIANT_MS = 3500;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Frames played in a loop while in view. An element marked `data-frames` shows in the frames it lists. Before the
// next frame, the control `data-presses` names for the frame looks pressed. Progress bars fill while their frame
// shows. A tab marked `data-tab-frames` is selected in its frames and jumps to the first. With reduced motion, the
// scene stays on its first frame.
export const scene = () => ({
  frame: 0,
  run: 0,
  inView: false,
  init() {
    this.animates = !matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!this.animates) return;
    new IntersectionObserver(([entry]) => {
      this.inView = entry.isIntersecting;
      this.play(this.frame);
    }).observe(this.$root);
  },
  get durations() {
    return this.$root.dataset.durations.split(",").map(Number);
  },
  jump(event) {
    const tab = event.target.closest("[data-tab-frames]");
    if (tab) this.play(Number(tab.dataset.tabFrames.split(" ")[0]));
  },
  showFrame(frame) {
    this.frame = frame;
    const shows = (element, attribute) => element.dataset[attribute].split(" ").includes(String(frame));
    for (const element of this.$root.querySelectorAll("[data-frames]")) element.hidden = !shows(element, "frames");
    for (const tab of this.$root.querySelectorAll("[data-tab-frames]")) {
      tab.setAttribute("aria-selected", String(shows(tab, "tabFrames")));
    }
    for (const control of this.$root.querySelectorAll("[data-pressed]")) control.removeAttribute("data-pressed");
    for (const bar of this.$root.querySelectorAll("progress")) fill(bar, this.durations[frame]);
  },
  // Plays from `frame` while in view; a later call takes over from an earlier one.
  async play(frame) {
    this.run += 1;
    const run = this.run;
    this.showFrame(frame);
    while (this.animates && this.inView && run === this.run) {
      await sleep(this.durations[this.frame] - PRESS_MS);
      if (run !== this.run) return;
      this.press(this.frame);
      await sleep(PRESS_MS);
      if (run !== this.run) return;
      this.showFrame((this.frame + 1) % this.durations.length);
    }
  },
  press(frame) {
    const selector = JSON.parse(this.$root.dataset.presses)[frame];
    if (!selector) return;
    const control = [...this.$root.querySelectorAll(selector)].find((each) => each.checkVisibility());
    control.setAttribute("data-pressed", "");
  },
});

// Fills `bar` from its value to the end over `ms`.
function fill(bar, ms) {
  const from = Number(bar.value);
  const start = performance.now();
  const update = (now) => {
    const done = Math.min((now - start) / ms, 1);
    bar.value = from + (1 - from) * done;
    if (done < 1) requestAnimationFrame(update);
  };
  requestAnimationFrame(update);
}

// The open answer group of a scene's answers column: its answers listed, or Adjust marks on and filled in.
export const openAnswerGroup = () => ({
  init() {
    const { marks, feedback, showAnswers } = this.$el.dataset;
    const group = this.$el.querySelector("details[open]");
    if (showAnswers !== undefined) {
      group.querySelector("[data-answers]").checked = true;
    }
    if (marks !== undefined) {
      group.querySelector("[data-adjust]").checked = true;
      group.querySelector("[name=marks]").value = marks;
      group.querySelector("[name=feedback]").value = feedback;
    }
  },
});

const descriptionLines = (description) => [...description.querySelectorAll("li, p")];
const codeLines = (description) =>
  [...description.querySelectorAll("pre")].flatMap((pre) => pre.textContent.split("\n"));

// A line's text, without the lists inside it.
function ownText(line) {
  const copy = line.cloneNode(true);
  for (const list of copy.querySelectorAll("ul, ol")) list.remove();
  return copy.textContent;
}

// Puts each line of `pre`'s code in its own element, marking those not in `previousLines` as new.
function markNewCodeLines(pre, previousLines) {
  const code = pre.querySelector("code");
  code.replaceChildren(
    ...code.textContent.split("\n").flatMap((line, index) => {
      const element = Object.assign(document.createElement("span"), { textContent: line });
      element.toggleAttribute("data-new", previousLines !== null && !previousLines.has(line));
      return index ? ["\n", element] : [element];
    }),
  );
}

// The method examples as a slideshow. While it plays and is in view, the example shown steps through the variants of
// its settings, then the next example starts; after the last, the first. Choosing an example or a variant, or going
// back or forward, carries on from there; only pausing stops it. With reduced motion, it starts paused.
export const methodSlideshow = () => ({
  playing: true,
  inView: false,
  run: 0,
  init() {
    this.playing = !matchMedia("(prefers-reduced-motion: reduce)").matches;
    new IntersectionObserver(([entry]) => {
      this.inView = entry.isIntersecting;
      this.play();
    }).observe(this.$root);
  },
  // Alpine's state would hold these as proxies, which don't compare equal.
  get examples() {
    return [...this.$root.querySelectorAll("[data-example]")];
  },
  get example() {
    return this.examples.find((example) => !example.hidden);
  },
  get variant() {
    return Number(this.example.querySelector("[data-variant-dot][aria-current]").dataset.variantDot);
  },
  get variantCount() {
    return this.example.querySelectorAll("[data-variant-dot]").length;
  },
  togglePlaying() {
    this.playing = !this.playing;
    this.play();
  },
  chooseExample() {
    this.showExample(this.$root.querySelector("[name=example]:checked").value, 0);
    this.play();
  },
  chooseVariant(event) {
    const dot = event.target.closest("[data-variant-dot]");
    if (!dot) return;
    this.showVariant(this.example, Number(dot.dataset.variantDot));
    this.play();
  },
  forward() {
    this.move(1);
    this.play();
  },
  back() {
    this.move(-1);
    this.play();
  },
  // Moves `by` variants through every example's variants, one example after another.
  move(by) {
    const variant = this.variant + by;
    if (variant >= 0 && variant < this.variantCount) {
      this.showVariant(this.example, variant);
      return;
    }
    const examples = this.examples;
    const next = examples[(examples.indexOf(this.example) + by + examples.length) % examples.length];
    this.$root.querySelector(`[name=example][value="${next.dataset.example}"]`).checked = true;
    this.showExample(next.dataset.example, by > 0 ? 0 : next.querySelectorAll("[data-variant-dot]").length - 1);
  },
  showExample(key, variant) {
    for (const example of this.examples) {
      example.hidden = example.dataset.example !== key;
    }
    this.showVariant(this.example, variant);
  },
  // Plays from the variant shown, while playing and in view; a later call takes over from an earlier one.
  async play() {
    this.run += 1;
    const run = this.run;
    for (const dot of this.$root.querySelectorAll("[data-timing]")) dot.removeAttribute("data-timing");
    while (this.playing && this.inView && run === this.run) {
      const duration = this.variant ? VARIANT_MS : FIRST_VARIANT_MS;
      this.$root.style.setProperty("--variant-ms", `${duration}ms`);
      this.restartDotTimer();
      await sleep(duration);
      if (run !== this.run) return;
      this.move(1);
    }
  },
  // Starts the shown variant's dot filling again.
  restartDotTimer() {
    const dot = this.example.querySelector("[data-variant-dot][aria-current]");
    dot.removeAttribute("data-timing");
    void dot.offsetWidth;
    dot.setAttribute("data-timing", "");
  },
  // Shows `variant` of `example`, marking the lines of its description, code included, that the variant before
  // doesn't have.
  showVariant(example, variant) {
    const description = () => example.querySelector(".landing-variant:not([hidden]) .prose-core");
    const before = description();
    const previousLines = new Set(before ? descriptionLines(before).map(ownText) : []);
    const previousCode = new Set(before ? codeLines(before) : []);
    for (const element of example.querySelectorAll("[data-variant]")) {
      element.hidden = element.dataset.variant !== String(variant);
    }
    for (const dot of example.querySelectorAll("[data-variant-dot]")) {
      if (dot.dataset.variantDot === String(variant)) {
        dot.setAttribute("aria-current", "step");
      } else {
        dot.removeAttribute("aria-current");
      }
    }
    const after = description();
    if (!after) return;
    for (const line of descriptionLines(after)) {
      line.toggleAttribute("data-new", variant > 0 && !previousLines.has(ownText(line)));
    }
    for (const pre of after.querySelectorAll("pre")) {
      markNewCodeLines(pre, variant > 0 ? previousCode : null);
    }
  },
});
