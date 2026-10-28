/** Exercise browser source remapping with real V8 ranges and inline esbuild maps. */
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import inspector from "node:inspector";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { pathToFileURL } from "node:url";
import vm from "node:vm";
import { createBrowserCoverage } from "./coverage.mjs";

const hostRequire = createRequire(new URL("../../../../web-next/package.json", import.meta.url));
const { build } = hostRequire("esbuild");
const moduleSource = [
  "export function choose(flag: boolean) {",
  "  if (flag) {",
  "    return 'yes';",
  "  }",
  "  return 'no';",
  "}",
  "export function dormant() {",
  "  return 'never';",
  "}",
  "",
].join("\n");

/** Ask the inspector for actual executed and unexecuted ranges in one fixture run. */
async function executeCoverage(source, generatedPath, flag) {
  const session = new inspector.Session();
  session.connect();
  const post = (method, options = {}) => new Promise((resolve, reject) =>
    session.post(method, options, (error, value) => error ? reject(error) : resolve(value)));
  try {
    await post("Profiler.enable");
    await post("Profiler.startPreciseCoverage", { callCount: true, detailed: true });
    const url = pathToFileURL(generatedPath).href;
    vm.runInNewContext(source, { fixtureFlag: flag }, { filename: url });
    const { result } = await post("Profiler.takePreciseCoverage");
    const entry = result.find(value => value.url === url);
    assert.ok(entry, "Synthetic bundle was not measured by V8");
    return { ...entry, url: "http://fixture.invalid/assets/fixture.js", source };
  } finally {
    await post("Profiler.stopPreciseCoverage");
    session.disconnect();
  }
}

/** Build an isolated module plus excluded fixture and host-library sources. */
async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "rider-browser-coverage-"));
  const original = path.join(root, "web-next", "lib", "fixture.ts");
  const entry = path.join(root, "tests", "browser", "entry.ts");
  const generatedPath = path.join(root, "test-results", "assets", "fixture.js");
  await mkdir(path.dirname(original), { recursive: true });
  await mkdir(path.dirname(entry), { recursive: true });
  await writeFile(original, moduleSource);
  await writeFile(path.join(root, "host.ts"), "export const library = 1;\n");
  await writeFile(entry, [
    'import * as fixture from "../../web-next/lib/fixture";',
    'import {library} from "../../host";',
    "globalThis.result = fixture.choose(globalThis.fixtureFlag) + library;",
  ].join("\n"));
  const result = await build({ entryPoints: [entry], outfile: generatedPath,
    bundle: true, treeShaking: false, format: "iife", sourcemap: "inline", write: false });
  return { root, generatedPath, source: result.outputFiles[0].text,
    bundles: new Map([["/assets/fixture.js", generatedPath]]) };
}

/** Read LCOV line and function counts without replacing real zeros. */
function counts(report) {
  const lines = new Map([...report.matchAll(/^DA:(\d+),(\d+)$/gm)].map(match => [Number(match[1]), Number(match[2])]));
  const functions = new Map([...report.matchAll(/^FNDA:(\d+),(.+)$/gm)].map(match => [match[2], Number(match[1])]));
  const branches = [...report.matchAll(/^BRDA:\d+,[^,]+,[^,]+,(\d+)$/gm)].map(match => Number(match[1]));
  return { lines, functions, branches };
}

test("browser LCOV maps original TS, preserves zeros, excludes libraries, and merges runs", async () => {
  const data = await fixture();
  try {
    const coverage = createBrowserCoverage(data);
    const yes = await executeCoverage(data.source, data.generatedPath, true);
    await coverage.add([{ url: "http://fixture.invalid/unmapped.js", source: "invalid" }, yes]);
    const first = counts(await readFile(await coverage.write(), "utf8"));
    assert.ok(first.lines.get(3) > 0, "Executed true branch lost its original TS line");
    assert.equal(first.lines.get(5), 0, "Unexecuted false branch became covered");
    assert.equal(first.lines.get(8), 0, "Uncalled function became covered");
    assert.equal(first.functions.get("choose"), 1);
    assert.equal(first.functions.get("dormant"), 0);
    assert.ok(first.branches.some(count => count === 0), "Uncovered V8 branch was removed");

    await coverage.add([await executeCoverage(data.source, data.generatedPath, false)]);
    const report = await readFile(await coverage.write(), "utf8");
    const merged = counts(report);
    assert.ok(merged.lines.get(3) > 0 && merged.lines.get(5) > 0, "Scenario merge discarded one executed branch");
    assert.equal(merged.lines.get(8), 0, "Merging runs fabricated coverage for the uncalled function");
    assert.equal(merged.functions.get("choose"), 2, "Execution counts were not added");
    assert.equal(merged.functions.get("dormant"), 0);
    assert.ok(merged.branches.some(count => count === 0));
    assert.deepEqual([...report.matchAll(/^SF:(.+)$/gm)].map(match => match[1]), ["web-next/lib/fixture.ts"]);
    assert.ok([...merged.lines.keys()].every(line => line <= moduleSource.split("\n").length));
  } finally {
    await rm(data.root, { recursive: true, force: true });
  }
});

test("browser coverage fails closed for empty reports, missing maps, and changed sources", async () => {
  const data = await fixture();
  try {
    const coverage = createBrowserCoverage(data);
    await assert.rejects(coverage.write(), /No module browser coverage/);
    await assert.rejects(coverage.add([{ url: "http://fixture.invalid/assets/fixture.js", source: "plain source", functions: [] }]), /inline source map/);
    await assert.rejects(coverage.add([{ url: "http://fixture.invalid/assets/fixture.js",
      source: "//# sourceMappingURL=data:application/json;base64,e30=", functions: [] }]), /version 3/);
    const entry = await executeCoverage(data.source, data.generatedPath, true);
    await assert.rejects(coverage.add([{ ...entry, functions: [{ ranges: [{ startOffset: 0, endOffset: 1, count: -1 }] }] }]), /execution count/);
    await writeFile(path.join(data.root, "web-next", "lib", "fixture.ts"), moduleSource + "// changed\n");
    await assert.rejects(coverage.add([entry]), /does not match current module source/);
  } finally {
    await rm(data.root, { recursive: true, force: true });
  }
});
