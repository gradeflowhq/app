// Monaco editors for code fields: `codeEditor` replaces a textarea, follows the page's theme and keeps the
// textarea's value in sync. `data-language` names the language; `data-fill` grows it to fill a flex column instead
// of fitting its content. Its Expand button opens the same code in the code dialog (base.html).

import { loadScript, vendorUrl } from "./vendor.js";

const darkScheme = matchMedia("(prefers-color-scheme: dark)");
const theme = () => {
  const chosen = document.documentElement.dataset.theme;
  return (chosen ? chosen === "dark" : darkScheme.matches) ? "vs-dark" : "vs";
};
let loading;

function loadMonaco() {
  loading ??= loadScript("monaco-0.57.0/vs/loader.js").then(() => {
    // Otherwise the other UMD libraries would define AMD modules instead of globals.
    delete define.amd;
    require.config({ paths: { vs: vendorUrl("monaco-0.57.0/vs") } });
    return new Promise((resolve) => {
      require(["vs/editor/editor.main"], () => {
        darkScheme.addEventListener("change", () => monaco.editor.setTheme(theme()));
        resolve();
      });
    });
  });
  return loading;
}

const options = {
  automaticLayout: true,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  wordWrap: "off",
  lineNumbersMinChars: 3,
  overviewRulerLanes: 0,
  overviewRulerBorder: false,
  hideCursorInOverviewRuler: true,
  folding: false,
  fontSize: 14,
  tabSize: 4,
  padding: { top: 10, bottom: 10 },
};

export const codeEditor = () => {
  let editor;
  let editorContainer;
  let expanded;
  return {
    async init() {
      const textarea = this.$el;
      const fill = "fill" in textarea.dataset;
      await loadMonaco();
      if (!textarea.isConnected) return;
      editorContainer = Object.assign(document.createElement("div"), {
        className: `relative w-full overflow-hidden rounded-field border border-base-300${fill ? " min-h-48 flex-1" : ""}`,
      });
      editorContainer.setAttribute("x-ignore", "");
      textarea.hidden = true;
      textarea.after(editorContainer);
      const label = textarea.labels?.[0]?.textContent.trim() || textarea.ariaLabel || "Code";
      editor = monaco.editor.create(editorContainer, {
        ...options,
        value: textarea.value,
        readOnly: textarea.readOnly || textarea.disabled,
        language: textarea.dataset.language || "plaintext",
        ariaLabel: label,
        theme: theme(),
      });
      if (!fill) {
        const fit = () => (editorContainer.style.height = `${Math.min(Math.max(editor.getContentHeight(), 180), 520)}px`);
        editor.onDidContentSizeChange(fit);
        fit();
      }
      const changed = () => textarea.dispatchEvent(new Event("change", { bubbles: true }));
      editor.onDidChangeModelContent(() => {
        textarea.value = editor.getValue();
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
      });
      editor.onDidBlurEditorText(changed);
      const expand = document.getElementById("code-expand").content.firstElementChild.cloneNode(true);
      expand.addEventListener("click", () => {
        const dialog = document.getElementById("code");
        const described = textarea.getAttribute("aria-describedby")?.split(" ") ?? [];
        dialog.querySelector("h2").textContent = label;
        dialog.querySelector("[data-detail]").textContent = described
          .map((id) => document.getElementById(id)?.textContent.trim())
          .filter(Boolean)
          .join(" ");
        expanded = monaco.editor.create(dialog.querySelector("[data-editor]"), {
          ...options,
          model: editor.getModel(),
          readOnly: editor.getOption(monaco.editor.EditorOption.readOnly),
          ariaLabel: label,
        });
        dialog.addEventListener(
          "close",
          () => {
            expanded.dispose();
            expanded = null;
            changed();
          },
          { once: true },
        );
        dialog.showModal();
        expanded.focus();
      });
      editorContainer.prepend(expand);
    },
    destroy() {
      if (expanded) document.getElementById("code").close();
      editor?.getModel()?.dispose();
      editor?.dispose();
      editorContainer?.remove();
    },
  };
};
