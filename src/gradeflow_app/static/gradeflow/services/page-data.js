// Data the server puts in a page with `json_script`.

/** The JSON in the element with `id`, or `fallback` without one. */
export function readJson(id, fallback = null) {
  const element = document.getElementById(id);
  return element ? JSON.parse(element.textContent) : fallback;
}
