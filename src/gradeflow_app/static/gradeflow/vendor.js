// Loads vendored libraries on first use. Static files serve them under their unhashed names too.

const scriptLoads = new Map();

export const vendorUrl = (path) => new URL(`../vendor/${path}`, import.meta.url).href;

/** Runs the script at `path` in the vendor folder, once. */
export function loadScript(path) {
  if (!scriptLoads.has(path)) {
    const element = Object.assign(document.createElement("script"), { src: vendorUrl(path) });
    const loading = new Promise((resolve, reject) => {
      element.addEventListener("load", resolve);
      element.addEventListener("error", () => reject(new Error(`Couldn't load ${path}`)));
    });
    document.head.append(element);
    scriptLoads.set(path, loading);
  }
  return scriptLoads.get(path);
}
