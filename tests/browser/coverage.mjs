/** Convert fixture-only Chromium coverage to original module sources. */
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(new URL("../web-independent/package.json", import.meta.url));
const v8ToIstanbul = require("v8-to-istanbul");
const { createCoverageMap } = require("istanbul-lib-coverage");
const { createContext } = require("istanbul-lib-report");
const reports = require("istanbul-reports");

/** Decode and validate the inline esbuild map required for original TS coverage. */
function inlineMap(source) {
  const match = source.match(/\/\/[#@]\s*sourceMappingURL=data:application\/json(?:;charset=[^;,\s]+)?;base64,([A-Za-z0-9+/=]+)\s*$/);
  assert.ok(match, "Mapped browser bundle must contain an inline source map");
  const map = JSON.parse(Buffer.from(match[1], "base64").toString("utf8"));
  assert.equal(map.version, 3, "Browser source map must use version 3");
  assert.ok(Array.isArray(map.sources) && map.sources.length, "Browser source map has no sources");
  assert.equal(typeof map.mappings, "string", "Browser source map has no mappings");
  assert.ok(map.mappings.length, "Browser source map has empty mappings");
  assert.ok(Array.isArray(map.sourcesContent), "Browser source map must include original source contents");
  assert.equal(map.sourcesContent.length, map.sources.length, "Browser source map contents do not match sources");
  assert.ok(map.sourcesContent.every(content => typeof content === "string"), "Browser source map has missing source contents");
  return map;
}

/** Resolve a map source using the same generated-file base as v8-to-istanbul. */
function originalPath(generatedPath, map, source) {
  if (source.startsWith("file://")) return fileURLToPath(source);
  const sourceRoot = map.sourceRoot ?? "";
  return path.resolve(path.dirname(generatedPath), sourceRoot, source);
}

/** Reject malformed V8 counts instead of producing an optimistic partial report. */
function validateFunctions(functions, source) {
  assert.ok(Array.isArray(functions) && functions.length, "Mapped browser script has no V8 functions");
  for (const fn of functions) {
    assert.ok(Array.isArray(fn.ranges) && fn.ranges.length, "Browser V8 function has no ranges");
    for (const range of fn.ranges) {
      assert.ok(Number.isInteger(range.startOffset) && range.startOffset >= 0, "Invalid browser V8 range start");
      assert.ok(Number.isInteger(range.endOffset) && range.endOffset > range.startOffset && range.endOffset <= source.length, "Invalid browser V8 range end");
      assert.ok(Number.isInteger(range.count) && range.count >= 0, "Invalid browser V8 execution count");
    }
  }
}

/** Accumulate original TS/TSX coverage from explicitly registered fixture bundles. */
export function createBrowserCoverage({ root, bundles }) {
  const projectRoot = path.resolve(root);
  const webRoot = path.join(projectRoot, "web-next");
  const coverage = createCoverageMap({});
  const observedSources = new Map();

  /** Limit published coverage to this module, excluding fixtures and host libraries. */
  function moduleSource(sourcePath) {
    const relative = path.relative(webRoot, sourcePath);
    return relative !== "" && relative !== ".." && !relative.startsWith(".." + path.sep) &&
      !path.isAbsolute(relative) && /\.tsx?$/.test(sourcePath);
  }

  /** Validate original contents before accepting coverage from a compiled bundle. */
  async function verifyOriginals(generatedPath, map) {
    let included = false;
    for (const [index, source] of map.sources.entries()) {
      assert.equal(typeof source, "string", "Browser source map has an invalid source path");
      const resolved = originalPath(generatedPath, map, source);
      if (!moduleSource(resolved)) continue;
      assert.equal(await readFile(resolved, "utf8"), map.sourcesContent[index], "Browser source map does not match current module source: " + resolved);
      included = true;
    }
    return included;
  }

  return {
    /** Merge one scenario's real Chromium ranges without changing their counts. */
    async add(entries) {
      assert.ok(Array.isArray(entries), "Browser coverage entries must be an array");
      for (const entry of entries) {
        const generatedPath = bundles.get(new URL(entry.url, "http://fixture.invalid").pathname);
        if (!generatedPath) continue;
        assert.ok(path.isAbsolute(generatedPath), "Browser bundle coverage path must be absolute");
        assert.ok(typeof entry.source === "string" && entry.source.length, "Mapped browser script has no source");
        const previous = observedSources.get(generatedPath);
        assert.ok(previous === undefined || previous === entry.source, "Browser bundle changed during coverage collection");
        const map = inlineMap(entry.source);
        if (!await verifyOriginals(generatedPath, map)) continue;
        validateFunctions(entry.functions, entry.source);
        const converter = v8ToIstanbul(generatedPath, 0, { source: entry.source }, source =>
          !moduleSource(originalPath(generatedPath, map, source)));
        await converter.load();
        converter.applyCoverage(entry.functions);
        const converted = converter.toIstanbul();
        assert.ok(Object.keys(converted).length, "Browser bundle produced no module source coverage");
        for (const source of Object.keys(converted)) {
          assert.ok(moduleSource(source), "Browser coverage escaped the module sources");
        }
        coverage.merge(converted);
        observedSources.set(generatedPath, entry.source);
      }
    },
    /** Write a nonempty Sonar-compatible LCOV report after every scenario succeeds. */
    async write() {
      assert.ok(coverage.files().length && coverage.getCoverageSummary().lines.total > 0, "No module browser coverage was collected");
      const directory = path.join(projectRoot, "test-results", "web-coverage");
      await mkdir(directory, { recursive: true });
      const reportPath = path.join(directory, "lcov-browser.info");
      reports.create("lcovonly", { file: "lcov-browser.info", projectRoot }).execute(
        createContext({ dir: directory, coverageMap: coverage }));
      const rawReport = await readFile(reportPath, "utf8");
      const report = rawReport.replace(/^SF:(.+)$/gm, (_, source) => "SF:" + source.split(path.sep).join("/"));
      if (report !== rawReport) await writeFile(reportPath, report);
      assert.match(report, /^SF:web-next\//m, "Browser LCOV has no original module paths");
      assert.match(report, /^DA:\d+,\d+$/m, "Browser LCOV has no line execution counts");
      return reportPath;
    },
  };
}
