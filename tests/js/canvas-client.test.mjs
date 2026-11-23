import assert from "node:assert/strict";
import { test } from "node:test";

import {
  canvasClient,
  displayedGrade,
  gradebookGrade,
  gradeFor,
  matchStudents,
} from "../../src/gradeflow_app/static/gradeflow/lib/canvas-client.js";

const jsonResponse = (body, headers = {}) =>
  new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json", ...headers } });

const stubFetch = (t, respond) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init = {}) => {
    calls.push({ url, ...init, body: init.body && JSON.parse(init.body) });
    return respond(url, init);
  });
  return calls;
};

/** Answers the course request `students` makes first, without a count of students, and `respond` to the rest. */
const withoutCount = (respond) => (url, init) =>
  new URL(url).pathname.endsWith("/courses/5") ? jsonResponse({}) : respond(url, init);
const usersCalls = (calls) => calls.filter(({ url }) => new URL(url).pathname.endsWith("/users"));

const client = () =>
  canvasClient({ baseUrl: "https://canvas.example.edu/", token: "secret", proxyUrl: "https://proxy.example.org/" });

test("requests go through the proxy with the token and the Canvas host", async (t) => {
  const calls = stubFetch(t, () => jsonResponse({ id: 7, workflow_state: "completed" }));
  assert.deepEqual(await client().progress(7), { id: 7, workflow_state: "completed" });
  assert.equal(calls[0].url, "https://proxy.example.org/api/v1/progress/7");
  assert.deepEqual(calls[0].headers, { Authorization: "Bearer secret", "X-Host": "canvas.example.edu" });
});

test("a request gives up once Canvas takes a minute", async (t) => {
  const calls = stubFetch(t, () => jsonResponse({}));
  const timeout = t.mock.method(AbortSignal, "timeout");
  await client().progress(7);
  assert.deepEqual(timeout.mock.calls[0].arguments, [60_000]);
  assert.equal(calls[0].signal, timeout.mock.calls[0].result);
});

test("keeps a Canvas base path", async (t) => {
  const calls = stubFetch(t, () => jsonResponse({}));
  await canvasClient({
    baseUrl: "https://lms.example.edu/canvas",
    token: "t",
    proxyUrl: "https://proxy.example.org",
  }).progress(1);
  assert.equal(calls[0].url, "https://proxy.example.org/canvas/api/v1/progress/1");
});

test('list endpoints follow Link rel="next" through the proxy', async (t) => {
  const next = "https://canvas.example.edu/api/v1/courses/5/users?page=bookmark:abc&per_page=100";
  const calls = stubFetch(
    t,
    withoutCount((url) =>
      url.includes("bookmark")
        ? jsonResponse([{ id: 3 }], {
            Link: `<${next}>; rel="current",<https://canvas.example.edu/api/v1/courses/5/users?page=first>; rel="first"`,
          })
        : jsonResponse([{ id: 1 }, { id: 2 }], {
            Link: `<https://canvas.example.edu/api/v1/courses/5/users?page=first>; rel="current",<${next}>; rel="next"`,
          }),
    ),
  );

  assert.deepEqual(await client().students(5), [{ id: 1 }, { id: 2 }, { id: 3 }]);
  const [first, second] = usersCalls(calls).map(({ url }) => new URL(url));
  assert.equal(first.origin + first.pathname, "https://proxy.example.org/api/v1/courses/5/users");
  assert.deepEqual(first.searchParams.getAll("enrollment_type[]"), ["student"]);
  assert.deepEqual(first.searchParams.getAll("include[]"), ["login_id", "sis_user_id", "integration_id", "email"]);
  assert.equal(first.searchParams.get("per_page"), "100");
  assert.equal(second.href, "https://proxy.example.org/api/v1/courses/5/users?page=bookmark:abc&per_page=100");
});

test("pages Canvas numbers come at once after the first, in order", async (t) => {
  const pageOf = (url) => Number(new URL(url).searchParams.get("page") ?? 1);
  let pending = 0;
  let mostPending = 0;
  const calls = stubFetch(
    t,
    withoutCount(async (url) => {
      mostPending = Math.max(mostPending, ++pending);
      await new Promise((resolve) => setTimeout(resolve, 10 * (4 - pageOf(url))));
      pending--;
      return jsonResponse([{ id: pageOf(url) }], {
        Link: '<https://canvas.example.edu/api/v1/courses/5/users?page=3&per_page=100>; rel="last"',
      });
    }),
  );
  assert.deepEqual(await client().students(5), [{ id: 1 }, { id: 2 }, { id: 3 }]);
  assert.deepEqual(
    usersCalls(calls).map(({ url }) => pageOf(url)),
    [1, 2, 3],
  );
  assert.equal(mostPending, 2);
});

test("students come at once, in the pages the course's count of them fills", async (t) => {
  const pageOf = (url) => Number(new URL(url).searchParams.get("page") ?? 1);
  const people = (from, count) => Array.from({ length: count }, (_, i) => ({ id: from + i }));
  let pending = 0;
  let mostPending = 0;
  const calls = stubFetch(t, async (url) => {
    if (new URL(url).pathname.endsWith("/courses/5")) return jsonResponse({ id: 5, total_students: 250 });
    mostPending = Math.max(mostPending, ++pending);
    await new Promise((resolve) => setTimeout(resolve, 10));
    pending--;
    const page = pageOf(url);
    return jsonResponse(people((page - 1) * 100, page === 3 ? 50 : 100));
  });
  assert.deepEqual(await client().students(5), people(0, 250));
  assert.deepEqual(new URL(calls[0].url).searchParams.getAll("include[]"), ["total_students"]);
  assert.equal(mostPending, 3);
});

test("the page after the count comes with the others, and a full one fetches the next", async (t) => {
  const calls = stubFetch(t, (url) => {
    if (new URL(url).pathname.endsWith("/courses/5")) return jsonResponse({ id: 5, total_students: 100 });
    const page = Number(new URL(url).searchParams.get("page") ?? 1);
    return jsonResponse(Array.from({ length: page < 3 ? 100 : 2 }, (_, i) => ({ id: `${page}-${i}` })));
  });
  assert.equal((await client().students(5)).length, 202);
  assert.deepEqual(
    usersCalls(calls).map(({ url }) => new URL(url).searchParams.get("page")),
    [null, "2", "3"],
  );
});

test("a proxy that hides the Link header still gets every page, by number", async (t) => {
  const usersPage = (count) => Array.from({ length: count }, (_, i) => ({ id: i }));
  const calls = stubFetch(t, (url) =>
    jsonResponse(new URL(url).searchParams.get("page") === "2" ? usersPage(3) : usersPage(100)),
  );
  assert.equal((await client().courses()).length, 103);
  assert.deepEqual(
    calls.map(({ url }) => new URL(url).searchParams.get("page")),
    [null, "2"],
  );
});

test("courses come with their term and number of students", async (t) => {
  const calls = stubFetch(t, () => jsonResponse([]));
  await client().courses();
  assert.deepEqual(new URL(calls[0].url).searchParams.getAll("include[]"), ["term", "total_students"]);
});

test("a course comes on its own, to say whether it weights its assignment groups", async (t) => {
  const calls = stubFetch(t, () => jsonResponse({ id: 5, apply_assignment_group_weights: true }));
  assert.equal((await client().course(5)).apply_assignment_group_weights, true);
  assert.equal(new URL(calls[0].url).pathname, "/api/v1/courses/5");
});

test("assignment groups come with their assignments", async (t) => {
  const calls = stubFetch(t, () => jsonResponse([]));
  await client().assignmentGroups(5);
  const url = new URL(calls[0].url);
  assert.equal(url.pathname, "/api/v1/courses/5/assignment_groups");
  assert.deepEqual(url.searchParams.getAll("include[]"), ["assignments"]);
});

test("submissions are each student's score and comment texts, by user ID", async (t) => {
  const calls = stubFetch(t, () =>
    jsonResponse([
      { user_id: 1, score: 7.5, submission_comments: [{ comment: "Q1 [1/1]" }, { comment: "See me." }] },
      { user_id: 2, score: null, submission_comments: [] },
    ]),
  );
  assert.deepEqual(
    await client().submissions(5, 9),
    new Map([
      [1, { score: 7.5, comments: new Set(["Q1 [1/1]", "See me."]) }],
      [2, { score: null, comments: new Set() }],
    ]),
  );
  const url = new URL(calls[0].url);
  assert.equal(url.pathname, "/api/v1/courses/5/assignments/9/submissions");
  assert.deepEqual(url.searchParams.getAll("include[]"), ["submission_comments"]);
});

test("writes post JSON", async (t) => {
  const calls = stubFetch(t, () => jsonResponse({ id: 9 }));
  const canvas = client();
  await canvas.createAssignmentGroup(5, { name: "Exams", group_weight: 40 });
  await canvas.createAssignment(5, { name: "Midterm", points_possible: 40, assignment_group_id: 9 });
  await canvas.updateGrades(5, 11, { 101: { posted_grade: 30 } });

  assert.deepEqual(
    calls.map(({ method, url, body }) => [method, new URL(url).pathname, body]),
    [
      ["POST", "/api/v1/courses/5/assignment_groups", { name: "Exams", group_weight: 40 }],
      [
        "POST",
        "/api/v1/courses/5/assignments",
        {
          assignment: {
            submission_types: ["none"],
            grading_type: "points",
            published: true,
            name: "Midterm",
            points_possible: 40,
            assignment_group_id: 9,
          },
        },
      ],
      [
        "POST",
        "/api/v1/courses/5/assignments/11/submissions/update_grades",
        { grade_data: { 101: { posted_grade: 30 } } },
      ],
    ],
  );
  assert.equal(calls[0].headers["Content-Type"], "application/json");
});

test("groups and assignments are changed with PUT and deleted with DELETE", async (t) => {
  const calls = stubFetch(t, () => jsonResponse({ id: 9 }));
  const canvas = client();
  await canvas.updateAssignmentGroup(5, 7, { name: "Exams", group_weight: 50 });
  await canvas.deleteAssignmentGroup(5, 7, 8);
  await canvas.deleteAssignmentGroup(5, 7);
  await canvas.updateAssignment(5, 11, { name: "Midterm", points_possible: 40 });
  await canvas.deleteAssignment(5, 11);

  assert.deepEqual(
    calls.map(({ method, url, body }) => [method, new URL(url).pathname + new URL(url).search, body]),
    [
      ["PUT", "/api/v1/courses/5/assignment_groups/7", { name: "Exams", group_weight: 50 }],
      ["DELETE", "/api/v1/courses/5/assignment_groups/7?move_assignments_to=8", undefined],
      ["DELETE", "/api/v1/courses/5/assignment_groups/7", undefined],
      ["PUT", "/api/v1/courses/5/assignments/11", { assignment: { name: "Midterm", points_possible: 40 } }],
      ["DELETE", "/api/v1/courses/5/assignments/11", undefined],
    ],
  );
});

test("Canvas errors reject with Canvas's message and the status", async (t) => {
  stubFetch(t, () => new Response(JSON.stringify({ errors: [{ message: "Invalid access token." }] }), { status: 401 }));
  await assert.rejects(client().courses(), { message: "Invalid access token.", status: 401 });
  stubFetch(t, () => new Response("Bad gateway", { status: 502 }));
  await assert.rejects(client().courses(), { message: "Canvas request failed (502)", status: 502 });
});

test("a reply that is not Canvas's says it came from the proxy", async (t) => {
  stubFetch(
    t,
    () => new Response("Invalid or missing X-Host", { status: 400, headers: { "Content-Type": "text/plain" } }),
  );
  await assert.rejects(client().progress(1), { status: 400, proxy: true });
});

test("matchStudents matches identifiers case-insensitively", () => {
  const users = [
    { id: 1, name: "Ann Tan", login_id: "U1000001", sis_user_id: "S1000001", email: "ann@example.edu" },
    { id: 2, name: "Ben Lim", login_id: "u1000002", integration_id: "INT-2", email: "Ben@example.edu" },
    { id: 3, name: "Cat Ng", login_id: "shared" },
    { id: 4, name: "Dan Ong", sis_user_id: "shared", login_id: "dan", email: "dan" },
  ];
  const matchedUser = (studentId) => matchStudents([{ studentId }], users).matched[0]?.user.id;

  assert.deepEqual(
    ["s1000001", " u1000001 ", "ben@example.edu", "int-2", "shared", "DAN", "nobody", ""].map(matchedUser),
    [1, 1, 2, 2, undefined, 4, undefined, undefined],
  );
});

test("matchStudents matches a name nobody else has", () => {
  const users = [
    { id: 1, name: "Ann Tan", sortable_name: "Tan, Ann", login_id: "u1" },
    { id: 2, name: "John Lee", sortable_name: "Lee, John", login_id: "u2" },
    { id: 3, name: "John Lee", sortable_name: "Lee, John", login_id: "u3" },
    { id: 4, name: "u1", sortable_name: "u1", login_id: "u4" },
  ];
  const matchedUser = (studentId) => matchStudents([{ studentId }], users).matched[0]?.user.id;

  assert.deepEqual(["ann  tan", " Tan, Ann", "John Lee", "u1"].map(matchedUser), [1, 1, undefined, undefined]);
});

test("matchStudents lists users several rows match, with their rows, and users no row matches, in Canvas order", () => {
  const users = [
    { id: 1, name: "Ann Tan", login_id: "u1", email: "ann@example.edu" },
    { id: 2, name: "Ben Lim", login_id: "u2" },
    { id: 3, name: "Cat Ng", login_id: "u3" },
    { id: 4, name: "Dan Ong", login_id: "u4" },
  ];
  const rows = ["u3", "u1", "u2", "nobody", "Ann Tan"].map((studentId) => ({ studentId }));

  const { matched, unmatched, conflicts, unmatchedUsers } = matchStudents(rows, users);
  assert.deepEqual(
    matched.map(({ row, user }) => [row.studentId, user.id]),
    [
      ["u2", 2],
      ["u3", 3],
    ],
  );
  assert.deepEqual(unmatched, [rows[3]]);
  assert.deepEqual(conflicts, [{ user: users[0], rows: [rows[1], rows[4]] }]);
  assert.deepEqual(unmatchedUsers, [users[3]]);
});

test("gradeFor posts marks scaled to the assignment, or a percentage", () => {
  const row = { studentId: "x", marks: 30.3, maxMarks: 40 };
  assert.equal(gradeFor(row, { mode: "marks", rounding: null, pointsPossible: 40 }), 30.3);
  assert.equal(gradeFor(row, { mode: "marks", rounding: 0.5, pointsPossible: 40 }), 30.5);
  assert.equal(gradeFor(row, { mode: "marks", rounding: 1, pointsPossible: 100 }), 76);
  assert.equal(gradeFor(row, { mode: "marks", rounding: 0.1, pointsPossible: 40 }), 30.3);
  assert.equal(gradeFor(row, { mode: "percent", rounding: null, pointsPossible: 40 }), "75.75%");
  assert.equal(gradeFor(row, { mode: "percent", rounding: 1, pointsPossible: 40 }), "76%");
  assert.equal(gradeFor({ marks: 0, maxMarks: 40 }, { mode: "percent", rounding: null, pointsPossible: 40 }), "0%");
});

test("gradeFor gives null without a usable total", () => {
  const options = { mode: "marks", rounding: null, pointsPossible: 40 };
  assert.equal(gradeFor({ marks: null, maxMarks: 40 }, options), null);
  assert.equal(gradeFor({ marks: 3, maxMarks: 0 }, options), null);
});

test("displayedGrade shows points on a points assignment and a percentage on a percent one", () => {
  const points = { gradingType: "points", pointsPossible: 10 };
  assert.equal(displayedGrade(7.5, points), "7.5 / 10");
  assert.equal(displayedGrade("75.5%", points), "7.55 / 10");
  assert.equal(displayedGrade(0.1 + 0.2, points), "0.3 / 10");
  const percent = { gradingType: "percent", pointsPossible: 40 };
  assert.equal(displayedGrade(30.3, percent), "75.75%");
  assert.equal(displayedGrade("76%", percent), "76%");
});

test("gradebookGrade shows points without the total, as Canvas's gradebook does", () => {
  assert.equal(gradebookGrade("75.5%", { gradingType: "points", pointsPossible: 10 }), "7.55");
  assert.equal(gradebookGrade(30.3, { gradingType: "percent", pointsPossible: 40 }), "75.75%");
});
