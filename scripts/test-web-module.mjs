// Standalone module tests. The host integration test below is kept in the
// complete host suite: it imports React and jsdom from the Venom checkout and
// exercises use-rider against explicit host API/transport fixtures.
import { readdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("..", import.meta.url));
const directory = path.join(root, "tests", "web");
const hostIntegration = new Set(["figure_lifecycle.test.ts"]);
const available = readdirSync(directory).filter(name => name.endsWith(".test.ts"));
for (const file of hostIntegration) {
  if (!available.includes(file)) throw new Error(`Missing host integration test: ${file}`);
}
const files = available.filter(name => !hostIntegration.has(name)).sort();
if (!files.length) throw new Error("No standalone web tests found");
console.log(`Module web tests: ${files.length} files; host integration: ${[...hostIntegration].join(", ")}`);
const result = spawnSync(process.execPath, ["--import", path.join(root, "tests", "web-independent", "node_modules", "tsx", "dist", "loader.mjs"), "--test", ...files.map(name => path.join(directory, name))], {
  cwd: root,
  env: { ...process.env, NODE_PATH: path.join(root, "tests", "web-independent", "node_modules") },
  stdio: "inherit",
});
if (result.error) throw result.error;
if (result.signal) throw new Error(`Web tests terminated by signal ${result.signal}`);
process.exitCode = result.status ?? 1;
