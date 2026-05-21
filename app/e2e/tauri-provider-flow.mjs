import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import net from "node:net";

const appRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const resultPath = resolve(
  process.env.YUANBAO_TAURI_E2E_RESULT_PATH ||
    `${process.env.TEMP || process.env.TMP || appRoot}/yuanbao-tauri-provider-flow-result.json`,
);
const databasePath = resolve(
  process.env.YUANBAO_TAURI_E2E_DB_PATH ||
    `${process.env.TEMP || process.env.TMP || appRoot}/yuanbao-tauri-provider-flow.sqlite3`,
);
const timeoutMs = Number(process.env.YUANBAO_TAURI_E2E_TIMEOUT_MS || 300_000);
const apiKeyEnvVarName = process.env.YUANBAO_TAURI_E2E_API_KEY_ENV || "LOCAL_AGENT_PROVIDER_API_KEY";

async function findFreePort(preferred) {
  if (preferred) {
    return Number(preferred);
  }
  return new Promise((resolvePort, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 1420;
      server.close(() => resolvePort(port));
    });
  });
}

if (!process.env[apiKeyEnvVarName]) {
  console.error(`Missing provider API key env var: ${apiKeyEnvVarName}`);
  console.error(`Set ${apiKeyEnvVarName} before running this E2E harness.`);
  process.exit(2);
}

mkdirSync(dirname(resultPath), { recursive: true });
if (existsSync(resultPath)) {
  rmSync(resultPath, { force: true });
}
if (existsSync(databasePath)) {
  rmSync(databasePath, { force: true });
}

const env = {
  ...process.env,
  LOCAL_AGENT_DB_PATH: databasePath,
  YUANBAO_TAURI_E2E: "provider-flow",
  YUANBAO_TAURI_E2E_RESULT_PATH: resultPath,
  YUANBAO_TAURI_E2E_EXIT: process.env.YUANBAO_TAURI_E2E_EXIT || "1",
  YUANBAO_TAURI_E2E_API_KEY_ENV: apiKeyEnvVarName,
  YUANBAO_TAURI_E2E_BASE_URL:
    process.env.YUANBAO_TAURI_E2E_BASE_URL || "https://api.ximeixg.cloud/v1",
  YUANBAO_TAURI_E2E_MODEL:
    process.env.YUANBAO_TAURI_E2E_MODEL || "MiniMax-M2.7-highspeed",
};

const devPort = await findFreePort(process.env.YUANBAO_TAURI_E2E_DEV_PORT);
const tauriConfigPath = resolve(
  process.env.YUANBAO_TAURI_E2E_TAURI_CONFIG_PATH ||
    `${process.env.TEMP || process.env.TMP || appRoot}/yuanbao-tauri-provider-flow-${devPort}.json`,
);
writeFileSync(
  tauriConfigPath,
  JSON.stringify({
    build: {
      beforeDevCommand: `npm run dev -- --host 0.0.0.0 --port ${devPort}`,
      devUrl: `http://localhost:${devPort}`,
    },
  }),
  "utf-8",
);
const command = process.platform === "win32" ? "cmd.exe" : "npm";
const args = process.platform === "win32"
  ? ["/d", "/s", "/c", `npm run tauri:dev -- --config ${tauriConfigPath}`]
  : ["run", "tauri:dev", "--", "--config", tauriConfigPath];
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
  if (child.exitCode !== null || child.signalCode !== null) {
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
