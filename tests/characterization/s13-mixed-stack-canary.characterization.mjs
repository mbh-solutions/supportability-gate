import { mixedStackLabel } from "../../src/mixed_canary.ts";

console.log(JSON.stringify({
  behavior: { label: mixedStackLabel() },
  scenario: "s13-mixed-stack-canary",
  schema_version: "1.0",
}));
