// A Canvas REST client for the browser. Requests go through the CORS proxy, which forwards them to the host named in
// `X-Host`.

import { normalizedId } from "./cells.js";

const PAGE_SIZE = 100;
const TIMEOUT_MS = 60_000;
const IDENTIFIERS = ["login_id", "sis_user_id", "integration_id", "email"];

/** Canvas's array parameters are written `key[]=a&key[]=b`. */
const queryString = (params) =>
  new URLSearchParams(
    Object.entries(params).flatMap(([key, value]) =>
      Array.isArray(value) ? value.map((v) => [`${key}[]`, v]) : [[key, value]],
    ),
  ).toString();

/** The path and query of the Link header's next page, which the proxy needs instead of Canvas's URL. */
const nextPath = (link) => {
  const next = link?.match(/<([^>]*)>;\s*rel="next"/)?.[1];
  if (!next) return null;
  const { pathname, search } = new URL(next);
  return pathname + search;
};

const requestError = async (response) => {
  const body = await response.json().catch(() => undefined);
  const message = body?.errors?.[0]?.message ?? body?.message ?? `Canvas request failed (${response.status})`;
  // Canvas answers in JSON; anything else came from the proxy, such as a Canvas address it won't forward to.
  return Object.assign(new Error(message), { status: response.status, proxy: body === undefined });
};

export const canvasClient = ({ baseUrl, token, proxyUrl }) => {
  const canvas = new URL(baseUrl);
  const proxy = proxyUrl.replace(/\/+$/, "");
  const api = `${canvas.pathname.replace(/\/+$/, "")}/api/v1`;
  const headers = { Authorization: `Bearer ${token}`, "X-Host": canvas.host };

  /** `path` is the Canvas URL's path and query. */
  const request = async (path, init = {}) => {
    const response = await fetch(proxy + path, {
      signal: AbortSignal.timeout(TIMEOUT_MS),
      ...init,
      headers: { ...headers, ...init.headers },
    });
    if (!response.ok) throw await requestError(response);
    return response;
  };

  const get = async (path) => (await request(`${api}${path}`)).json();

  const post = async (path, body) =>
    (
      await request(`${api}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
    ).json();

  const list = async (path, params = {}) => {
    const items = [];
    const pageUrl = (page) =>
      `${api}${path}?${queryString({ ...params, per_page: PAGE_SIZE, ...(page > 1 && { page }) })}`;
    let page = 1;
    let next = pageUrl(page);
    while (next) {
      const response = await request(next);
      const pageItems = await response.json();
      items.push(...pageItems);
      next = nextPath(response.headers.get("Link"));
      // A proxy that doesn't expose the Link header hides the next page; a full page means there may be one.
      if (!response.headers.has("Link") && pageItems.length === PAGE_SIZE) next = pageUrl(++page);
    }
    return items;
  };

  return {
    courses: () => list("/courses", { include: ["term", "total_students"] }),
    course: (courseId) => get(`/courses/${courseId}`),
    assignments: (courseId) => list(`/courses/${courseId}/assignments`, { order_by: "name" }),
    assignmentGroups: (courseId) => list(`/courses/${courseId}/assignment_groups`),
    students: (courseId) =>
      list(`/courses/${courseId}/users`, {
        enrollment_type: ["student"],
        include: IDENTIFIERS,
      }),
    /** The comments on each student's submission, by user ID. */
    comments: async (courseId, assignmentId) =>
      new Map(
        (
          await list(`/courses/${courseId}/assignments/${assignmentId}/submissions`, {
            include: ["submission_comments"],
          })
        ).map((submission) => [
          submission.user_id,
          new Set(submission.submission_comments.map(({ comment }) => comment)),
        ]),
      ),
    createAssignmentGroup: (courseId, group) => post(`/courses/${courseId}/assignment_groups`, group),
    createAssignment: (courseId, assignment) =>
      post(`/courses/${courseId}/assignments`, {
        assignment: { submission_types: ["none"], grading_type: "points", published: true, ...assignment },
      }),
    /** Resolves to a Progress; poll it with `progress(id)`. */
    updateGrades: (courseId, assignmentId, gradeData) =>
      post(`/courses/${courseId}/assignments/${assignmentId}/submissions/update_grades`, { grade_data: gradeData }),
    progress: (id) => get(`/progress/${id}`),
  };
};

/** Rows matched to Canvas users by login ID, SIS ID, integration ID or email, and the rows that match nobody. An
 * identifier several users share matches none of them: a grade must never reach the wrong student. */
export const matchStudents = (rows, canvasUsers) => {
  const owners = new Map();
  for (const user of canvasUsers) {
    for (const key of IDENTIFIERS.map((field) => normalizedId(user[field])).filter(Boolean)) {
      owners.set(key, owners.has(key) && owners.get(key) !== user ? null : user);
    }
  }
  const matched = [];
  const unmatched = [];
  for (const row of rows) {
    const user = owners.get(normalizedId(row.studentId));
    if (user) matched.push({ row, user });
    else unmatched.push(row);
  }
  return { matched, unmatched };
};

/** The `posted_grade` for `marks` out of `maxMarks`, or null without marks. Mode "marks" scales them to
 * `pointsPossible`; mode "percent" gives a percentage, which Canvas applies to any assignment's points. A `rounding`
 * rounds to its nearest multiple. */
export const gradeFor = ({ marks, maxMarks }, { mode, rounding, pointsPossible }) => {
  if (!Number.isFinite(marks) || !(maxMarks > 0)) return null;
  const value = (marks / maxMarks) * (mode === "percent" ? 100 : pointsPossible);
  const rounded = rounding ? Math.round(value / rounding) * rounding : value;
  const grade = Number(rounded.toFixed(10));
  return mode === "percent" ? `${grade}%` : grade;
};
