// Shares this member's course keys with members who don't have them (#pending-course-key-shares), on every page. A
// share that fails is tried again on the next page.

import { shareCourseKeys } from "../services/course-keys.js";
import { readJson } from "../services/page-data.js";

const PENDING = "pending-course-key-shares";

function sharePending() {
  const shares = readJson(PENDING, []);
  if (!shares.length) return;
  document.getElementById(PENDING).textContent = "[]"; // not removed: adding a member swaps it out of band
  shareCourseKeys(shares).catch(() => {});
}

export function start() {
  sharePending();
  document.addEventListener("htmx:after:settle", sharePending);
}
