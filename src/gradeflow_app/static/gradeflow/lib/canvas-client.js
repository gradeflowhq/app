// A Canvas REST client for the browser. Requests go through the CORS proxy, which forwards them to the host named in
// `X-Host`.

import { normalizedId } from "./cells.js";

const PAGE_SIZE = 100;
const TIMEOUT_MS = 60_000;
const IDENTIFIERS = ["login_id", "sis_user_id", "integration_id", "email"];
const MATCHED_FIELDS = [...IDENTIFIERS, "name", "sortable_name"];

/** Canvas's array parameters are written `key[]=a&key[]=b`. */
const queryString = (params) =>
  new URLSearchParams(
    Object.entries(params).flatMap(([key, value]) =>
      Array.isArray(value) ? value.map((v) => [`${key}[]`, v]) : [[key, value]],
    ),
  ).toString();

/** The URL of the Link header's `rel` page, or null. */
const linkedPage = (link, rel) => {
  const url = link?.match(new RegExp(`<([^>]*)>;\\s*rel="${rel}"`))?.[1];
  return url ? new URL(url) : null;
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

  const requestJson = async (method, path, body) =>
    (
      await request(`${api}${path}`, {
        method,
        ...(body && { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
      })
    ).json();

  /** Every page of `path`. Pages come at once when their number is known: from `count`, the number of items to
   * expect, with one page more for a full last page, or from the first page's `last` link. Otherwise each page's
   * `next` link is followed. */
  const list = async (path, params = {}, count) => {
    const pageUrl = (page) =>
      `${api}${path}?${queryString({ ...params, per_page: PAGE_SIZE, ...(page > 1 && { page }) })}`;
    const fetchPage = async (page) => (await request(pageUrl(page))).json();
    const fetchPages = (from, to) => Promise.all(Array.from({ length: to - from + 1 }, (_, i) => fetchPage(from + i)));
    if (count != null) {
      const pages = await fetchPages(1, Math.floor(count / PAGE_SIZE) + 1);
      // A full last page may have more after it, such as students who joined since the count.
      while (pages.at(-1).length === PAGE_SIZE) pages.push(await fetchPage(pages.length + 1));
      return pages.flat();
    }
    const first = await request(pageUrl(1));
    const items = await first.json();
    const lastPage = Number(linkedPage(first.headers.get("Link"), "last")?.searchParams.get("page"));
    if (lastPage > 1) return items.concat(...(await fetchPages(2, lastPage)));
    let response = first;
    let pageItems = items;
    for (let page = 2; ; page++) {
      const next = linkedPage(response.headers.get("Link"), "next");
      // A proxy that doesn't expose the Link header hides the next page; a full page means there may be one.
      const hiddenNext = !response.headers.has("Link") && pageItems.length === PAGE_SIZE;
      if (!next && !hiddenNext) return items;
      response = await request(next ? next.pathname + next.search : pageUrl(page));
      pageItems = await response.json();
      items.push(...pageItems);
    }
  };

  return {
    courses: () => list("/courses", { include: ["term", "total_students"] }),
    course: (courseId) => get(`/courses/${courseId}`),
    /** The course's assignment groups in Canvas's order, each with its assignments. */
    assignmentGroups: (courseId) => list(`/courses/${courseId}/assignment_groups`, { include: ["assignments"] }),
    /** The course's students. Canvas doesn't say how many pages they fill, so the course's count of them does. */
    students: async (courseId) => {
      const course = await get(`/courses/${courseId}?${queryString({ include: ["total_students"] })}`);
      return list(
        `/courses/${courseId}/users`,
        { enrollment_type: ["student"], include: IDENTIFIERS },
        course.total_students,
      );
    },
    /** Each student's score and the texts of the comments on their submission, by user ID. */
    submissions: async (courseId, assignmentId) =>
      new Map(
        (
          await list(`/courses/${courseId}/assignments/${assignmentId}/submissions`, {
            include: ["submission_comments"],
          })
        ).map((submission) => [
          submission.user_id,
          {
            score: submission.score,
            comments: new Set(submission.submission_comments.map(({ comment }) => comment)),
          },
        ]),
      ),
    createAssignmentGroup: (courseId, group) => requestJson("POST", `/courses/${courseId}/assignment_groups`, group),
    updateAssignmentGroup: (courseId, groupId, group) =>
      requestJson("PUT", `/courses/${courseId}/assignment_groups/${groupId}`, group),
    /** Deletes the group with its assignments, or moves them to the group `moveAssignmentsTo` first. */
    deleteAssignmentGroup: (courseId, groupId, moveAssignmentsTo) => {
      const query = moveAssignmentsTo ? `?${queryString({ move_assignments_to: moveAssignmentsTo })}` : "";
      return requestJson("DELETE", `/courses/${courseId}/assignment_groups/${groupId}${query}`);
    },
    createAssignment: (courseId, assignment) =>
      requestJson("POST", `/courses/${courseId}/assignments`, {
        assignment: { submission_types: ["none"], grading_type: "points", published: true, ...assignment },
      }),
    updateAssignment: (courseId, assignmentId, assignment) =>
      requestJson("PUT", `/courses/${courseId}/assignments/${assignmentId}`, { assignment }),
    deleteAssignment: (courseId, assignmentId) =>
      requestJson("DELETE", `/courses/${courseId}/assignments/${assignmentId}`),
    /** Resolves to a Progress; poll it with `progress(id)`. */
    updateGrades: (courseId, assignmentId, gradeData) =>
      requestJson("POST", `/courses/${courseId}/assignments/${assignmentId}/submissions/update_grades`, {
        grade_data: gradeData,
      }),
    progress: (id) => get(`/progress/${id}`),
  };
};

const matchKey = (value) => normalizedId(value).replace(/\s+/g, " ");

/** Rows matched to Canvas users by login ID, SIS ID, integration ID, email or name; rows that match nobody; users
 * several rows match, with those rows; and users no row matches. All but `unmatched` follow the users' order. A value
 * several users share matches none of them: a grade must never reach the wrong student. */
export const matchStudents = (rows, canvasUsers) => {
  const owners = new Map();
  for (const user of canvasUsers) {
    for (const key of MATCHED_FIELDS.map((field) => matchKey(user[field])).filter(Boolean)) {
      owners.set(key, owners.has(key) && owners.get(key) !== user ? null : user);
    }
  }
  const matches = [];
  const unmatched = [];
  for (const row of rows) {
    const user = owners.get(matchKey(row.studentId));
    if (user) matches.push({ row, user });
    else unmatched.push(row);
  }
  const matchesByUser = Map.groupBy(matches, ({ user }) => user);
  const matched = [];
  const conflicts = [];
  const unmatchedUsers = [];
  for (const user of canvasUsers) {
    const userMatches = matchesByUser.get(user);
    if (!userMatches) unmatchedUsers.push(user);
    else if (userMatches.length === 1) matched.push(...userMatches);
    else conflicts.push({ user, rows: userMatches.map(({ row }) => row) });
  }
  return { matched, unmatched, conflicts, unmatchedUsers };
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

const toTwoDecimals = (value) => Number(value.toFixed(2));

/** The grade Canvas's gradebook shows for `postedGrade`: a percentage on a "percent" assignment, otherwise points,
 * to 2 decimal places. Canvas reads a "%" grade as that share of `pointsPossible`. */
export const gradebookGrade = (postedGrade, { gradingType, pointsPossible }) => {
  const points =
    typeof postedGrade === "string" ? (Number.parseFloat(postedGrade) / 100) * pointsPossible : postedGrade;
  return gradingType === "percent"
    ? `${toTwoDecimals((points / pointsPossible) * 100)}%`
    : String(toTwoDecimals(points));
};

/** The grade Canvas shows a student for `postedGrade`: as the gradebook does, out of `pointsPossible` on a points
 * assignment. */
export const displayedGrade = (postedGrade, { gradingType, pointsPossible }) => {
  const grade = gradebookGrade(postedGrade, { gradingType, pointsPossible });
  return gradingType === "percent" ? grade : `${grade} / ${toTwoDecimals(pointsPossible)}`;
};
