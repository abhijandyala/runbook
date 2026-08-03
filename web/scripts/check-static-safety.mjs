import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const sourceRoot = join(root, "src");

function sourceFiles(directory) {
  return readdirSync(directory)
    .flatMap((name) => {
      const path = join(directory, name);
      return statSync(path).isDirectory() ? sourceFiles(path) : [path];
    })
    .filter((path) => /\.(?:ts|tsx|js|jsx)$/.test(path));
}

const failures = [];
const files = sourceFiles(sourceRoot);

for (const path of files) {
  const source = readFileSync(path, "utf8");
  const label = relative(root, path);

  if (/dangerouslySetInnerHTML|\.innerHTML\s*=/.test(source)) {
    failures.push(`${label}: unsafe HTML rendering is forbidden`);
  }

  for (const tag of source.matchAll(/<a\b[^>]*target=["']_blank["'][^>]*>/g)) {
    if (!/rel=["'][^"']*\bnoopener\b[^"']*["']/.test(tag[0])) {
      failures.push(`${label}: external target="_blank" link lacks rel="noopener"`);
    }
  }
}

const contracts = readFileSync(join(sourceRoot, "contracts.ts"), "utf8");
const api = readFileSync(join(sourceRoot, "api.ts"), "utf8");
const app = readFileSync(join(sourceRoot, "App.tsx"), "utf8");
const store = readFileSync(join(sourceRoot, "store.tsx"), "utf8");

for (const [label, source, required] of [
  ["contracts.ts", contracts, "connector_live_writes_enabled"],
  ["contracts.ts", contracts, "github_repo_allowed"],
  ["api.ts", api, "execute_live_writes"],
  ["api.ts", api, "live_writes_confirmation"],
  ["api.ts", api, '"connector_action"'],
  ["api.ts", api, "stableSlackMessageId"],
  ["App.tsx", app, "safeExternalUrl"],
  ["App.tsx", app, 'rel="noopener noreferrer"'],
  ["App.tsx", app, 'title="No active incident"'],
  ["App.tsx", app, "hasIncidentContext"],
  ["store.tsx", store, 'document.visibilityState === "visible"'],
  ["store.tsx", store, 'document.addEventListener("visibilitychange", poll)'],
  ["store.tsx", store, "window.setInterval(poll, 4_500)"],
  ["store.tsx", store, "messagesRequestRef"],
  ["store.tsx", store, "activeRequest?.abort()"]
]) {
  if (!source.includes(required)) failures.push(`${label}: missing safety requirement ${required}`);
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exitCode = 1;
} else {
  console.log(`Static frontend safety checks passed (${files.length} source files).`);
}
