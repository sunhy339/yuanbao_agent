import { existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const appRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const resultPath = resolve(
  process.env.YUANBAO_TAURI_E2E_RESULT_PATH ||
    `${process.env.TEMP || process.env.TMP || appRoot}/yuanbao-tauri-provider-flow-result.json`,
);
const timeoutMs = Number(process.env.YUANBAO_TAURI_E2E_TIMEOUT_MS || 300_000);
const apiKeyEnvVarName = process.env.YUANBAO_TAURI_E2E_API_KEY_ENV || "LOCAL_AGENT_PROVIDER_API_KEY";

if (!process.env[apiKeyEnvVarName]) {
  console.error(`Missing provider API key env var: ${apiKeyEnvVarName}`);
  console.error(`Set ${apiKeyEnvVarName} before running this E2E harness.`);
  process.exit(2);
}

mkdirSync(dirname(resultPath), { recursive: true });
if (existsSync(resultPath)) {
  rmSync(resultPath, { force: true });
}

const env = {
  ...process.env,
  YUANBAO_TAURI_E2E: "provider-flow",
  YUANBAO_TAURI_E2E_RESULT_PATH: resultPath,
  YUANBAO_TAURI_E2E_EXIT: process.env.YUANBAO_TAURI_E2E_EXIT || "1",
  YUANBAO_TAURI_E2E_API_KEY_ENV: apiKeyEnvVarName,
  YUANBAO_TAURI_E2E_BASE_URL:
    process.env.YUANBAO_TAURI_E2E_BASE_URL || "https://api.ximeixg.cloud/v1",
  YUANBAO_TAURI_E2E_MODEL:
    process.env.YUANBAO_TAURI_E2E_MODEL || "MiniMax-M2.7-highspeed",
};

const command = process.platform === "win32" ? "cmd.exe" : "npm";
const args = process.platform === "win32"
  ? ["/d", "/s", "/c", "npm run tauri:dev"]
  : ["run", "tauri:dev"];
const child = spawn(command, args, {
  cwd: appRoot,
  env,
  stdio: ["ignore", "pipe", "pipe"],
});

let stdout = "";
let stderr = "";
child.stdout.on("data", (chunk) => {
  stdout += chunk.toString();
});
child.stderr.on("data", (chunk) => {
  stderr += chunk.toString();
});

function killTree() {
  if (child.exitCode !== null) {
    return;
  }
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
}

function tail(value, max = 5000) {
  return value.length > max ? value.slice(value.length - max) : value;
}

const start = Date.now();
let exitCode = 1;

try {
  while (Date.now() - start < timeoutMs) {
    if (existsSync(resultPath)) {
      const result = JSON.parse(readFileSync(resultPath, "utf-8"));
      console.log(JSON.stringify(result, null, 2));
      exitCode = result.ok ? 0 : 1;
      break;
    }

    if (child.exitCode !== null) {
      break;
    }

    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  if (!existsSync(resultPath)) {
    console.error(`Tauri provider E2E did not produce a result within ${timeoutMs}ms.`);
    console.error("--- tauri stdout tail ---");
    console.error(tail(stdout));
    console.error("--- tauri stderr tail ---");
    console.error(tail(stderr));
    exitCode = 1;
  }
} finally {
  killTree();
}

process.exit(exitCode);
