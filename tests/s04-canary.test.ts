import assert from "node:assert/strict";
import test from "node:test";
import { broken } from "../src/s04-canary.ts";

test("never-merge TypeScript suppression canary", () => {
  assert.equal(broken, 1);
});
