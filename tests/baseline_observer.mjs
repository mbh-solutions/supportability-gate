// Fixed V8 observer. Drivers must await their exercised asynchronous work before returning.
import { Session } from "node:inspector/promises";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath, pathToFileURL } from "node:url";
import { resolve, relative, sep } from "node:path";

const session = new Session();
session.connect();
await session.post("Profiler.enable");
await session.post("Profiler.startPreciseCoverage", { callCount: true, detailed: true });
const write = process.stdout.write.bind(process.stdout);
let output = "";
process.stdout.write = (chunk, encoding, callback) => {
  output += Buffer.isBuffer(chunk) ? chunk.toString("utf8") : chunk;
  if (typeof encoding === "function") encoding();
  if (typeof callback === "function") callback();
  return true;
};
await import(pathToFileURL(process.argv[2]).href);
const { result } = await session.post("Profiler.takePreciseCoverage");
await session.post("Profiler.stopPreciseCoverage");
session.disconnect();
const witness = {};
const target = resolve(process.env.SUPPORTABILITY_CHARACTERIZATION_TARGET);
for (const script of result) {
  if (!script.url.startsWith("file://")) continue;
  const filename = fileURLToPath(script.url);
  const path = relative(target, filename).split(sep).join("/");
  if (path.startsWith("../") || path === "..") continue;
  const execution = script.functions.flatMap(fn => {
    const range = fn.ranges[0];
    return range.count > 0 ? [[range.startOffset, range.endOffset, range.count]] : [];
  });
  witness[path] = {
    sha256: createHash("sha256").update(readFileSync(filename)).digest("hex"),
    execution,
  };
}
write(JSON.stringify({ driver: JSON.parse(output), witness }) + "\n");
