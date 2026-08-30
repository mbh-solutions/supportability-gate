import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const source = resolve(process.env.SUPPORTABILITY_CHARACTERIZATION_TARGET, "web/mixed_canary.ts");
if (existsSync(source)) {
  const { mixedStackLabel } = await import(pathToFileURL(source));
  if (mixedStackLabel() !== "python+typescript") {
    throw new Error("mixed stack label mismatch");
  }
}

console.log(JSON.stringify({
  behavior: { label: "python+typescript" },
  scenario: "s13-mixed-stack-canary",
  schema_version: "1.0",
}));
