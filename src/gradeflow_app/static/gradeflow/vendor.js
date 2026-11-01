// Loads vendored libraries on first use. Static files serve them under their unhashed names too.

const loaded = new Map();

export const vendor = (path) => new URL(`../vendor/${path}`, import.meta.url).href;

/** Runs the script at `path` in the vendor folder, once. */
export function script(path) {
  if (!loaded.has(path)) {
    const element = Object.assign(document.createElement("script"), { src: vendor(path) });
    const done = new Promise((resolve, reject) => {
      element.addEventListener("load", resolve);
      element.addEventListener("error", () => reject(new Error(`Couldn't load ${path}`)));
    });
    document.head.append(element);
    loaded.set(path, done);
  }
  return loaded.get(path);
}
