import assert from "node:assert/strict";
import { test } from "node:test";

import { decodeText } from "../../src/gradeflow_app/static/gradeflow/lib/text-files.js";

test("text is UTF-8, else Windows-1252; NUL bytes mean binary", () => {
  assert.equal(decodeText(new TextEncoder().encode('﻿print("é")\r\n')), 'print("é")\n');
  assert.equal(decodeText(Uint8Array.of(0x23, 0x20, 0xe9, 0x0a)), "# é\n");
  assert.equal(decodeText(Uint8Array.of(0x89, 0x50, 0x00, 0x01)), null);
});
