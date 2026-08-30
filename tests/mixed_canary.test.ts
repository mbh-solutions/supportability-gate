import assert from "node:assert/strict";
import test from "node:test";

import { mixedStackLabel } from "../web/mixed_canary.ts";

test("mixed stack canary", () => {
  assert.equal(mixedStackLabel(), "python+typescript");
});
