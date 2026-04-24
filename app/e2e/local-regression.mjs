import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = resolve(appRoot, "..");
const apiKeyEnvVarName = process.env.YUANBAO_TAURI_E2E_API_KEY_ENV || "LOCAL_AGENT_PROVIDER_API_KEY";
const mode = process.argv.includes("--ci") ? "ci" : "mock";

function shellQuote(value) {
  if (!/[\s&()^|<>]/.test(value)) {
    return value;
  }
  return `"${value.replace(/"/g, '\\"')}"`;
}

function run(command, args, cwd) {
  const display = [command, ...args].join(" ");
  console.log(`\n> ${display}`);

  const result = process.platform === "win32"
    ? spawnSync("cmd.exe", ["/d", "/s", "/c", [command, ...args].map(shellQuote).join(" ")], {
        cwd,
        env: process.env,
        stdio: "inherit",
      })
    : spawnSync(command, args, {
        cwd,
        env: process.env,
        stdio: "inherit",
      });

  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function runMockRegression() {
  run("npm", ["test"], appRoot);
  run("npm", ["run", "typecheck"], appRoot);
  run("cargo", ["check"], resolve(appRoot, "src-tauri"));
  run("python", ["-m", "pytest", "runtime/tests", "-q"], repoRoot);
}

runMockRegression();

if (mode === "ci") {
  if (process.env[apiKeyEnvVarName]) {
    run("npm", ["run", "e2e:real"], appRoot);
  } else {
    console.log(`\nSkipping real provider E2E: ${apiKeyEnvVarName} is not set.`);
  }
}
