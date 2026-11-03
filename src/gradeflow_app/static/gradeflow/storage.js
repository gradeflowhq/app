// What this browser keeps. A browser can refuse storage, as some private modes do: nothing is found there then, and
// keeping or removing does nothing.

const safeStorage = (area) => ({
  get(key) {
    try {
      return window[area].getItem(key);
    } catch {
      return null;
    }
  },
  /** The JSON kept under `key`, or null if there's none or it can't be read. */
  json(key) {
    try {
      return JSON.parse(window[area].getItem(key));
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      window[area].setItem(key, value);
    } catch {}
  },
  remove(key) {
    try {
      window[area].removeItem(key);
    } catch {}
  },
});

export const local = safeStorage("localStorage");
export const session = safeStorage("sessionStorage");
