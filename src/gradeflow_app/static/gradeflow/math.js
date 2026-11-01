// Typesets the LaTeX in the `.math` elements of Markdown the server rendered, with KaTeX loaded on first use.

import { script } from "./vendor.js";

export const math = () => ({
  init() {
    // A live morph brings back the server's LaTeX, which is typeset again once the morph is done.
    this.$el.addEventListener("morph", () => queueMicrotask(() => this.typeset()));
    this.typeset();
  },
  async typeset() {
    const formulas = this.$el.querySelectorAll(".math");
    if (!formulas.length) return;
    await script("katex-0.19.0/katex.min.js");
    for (const formula of formulas) {
      katex.render(formula.textContent, formula, {
        displayMode: formula.classList.contains("block"),
        throwOnError: false,
      });
    }
  },
});
