import assert from "node:assert/strict";
import { test } from "node:test";

import { bounds, ticks } from "../../src/gradeflow_app/static/gradeflow/lib/histogram.js";

test("the marks axis spans 0 to the maximum marks, with room for a bar per mark", () => {
  assert.deepEqual(bounds([[0, 1, 4]], 6), [0, 6]);
  assert.deepEqual(bounds([[0, 0, 4]], 6), [-0.5, 6.5]);
});

test("the axis ends at the maximum marks, dropping a round tick too close to it", () => {
  assert.deepEqual(ticks([0, 1, 2, 3, 4, 5, 6], 6), [0, 1, 2, 3, 4, 5, 6]);
  assert.deepEqual(ticks([0, 10, 20, 30, 40, 50, 60], 63), [0, 10, 20, 30, 40, 50, 63]);
  assert.deepEqual(ticks([0, 5, 10, 15, 20], 22), [0, 5, 10, 15, 22]);
  assert.deepEqual(ticks([-1, 0, 1, 2], 1), [0, 1]);
});
