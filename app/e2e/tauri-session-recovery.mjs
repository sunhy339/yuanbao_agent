import { existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const appRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const tempRoot = process.env.TEMP || process.env.TMP || appRoot;
const databasePath = resolve(
  process.env.YUANBAO_TAURI_E2E_DB_PATH ||
    `${tempRoot}/yuanbao-tauri-session-recovery.sqlite3`,
);
const timeoutMs = Number(process.env.YUANBAO_TAURI_E2E_TIMEOUT_MS || 180_000);
const sessionTitle = process.env.YUANBAO_TAURI_E2E_SESSION_TITLE || "E2E Recovery Session";
const prompt = process.env.YUANBAO_TAURI_E2E_PROMPT ||
  `Session recovery seed ${Date.now()}: persist this user message across a desktop restart.`;

mkdirSync(dirname(databasePath), { recursive: true });
if (existsSync(databasePath)) {
  rmSync(databasePath, { force: true });
}

function tail(value, max = 5000) {
  return value.length > max ? value.slice(value.length - max) : value;
}

function killTree(child) {
  if (child.exitCode !== null) {
    return;
  }
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
}

async function runDesktopFlow(flow, resultPath) {
  if (existsSync(resultPath)) {
    rmSync(resultPath, { force: true });
  }

  const env = {
    ...process.env,
    LOCAL_AGENT_DB_PATH: databasePath,
    YUANBAO_TAURI_E2E: flow,
    YUANBAO_TAURI_E2E_RESULT_PATH: resultPath,
    YUANBAO_TAURI_E2E_EXIT: "1",
    YUANBAO_TAURI_E2E_SESSION_TITLE: sessionTitle,
    YUANBAO_TAURI_E2E_PROMPT: prompt,
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

  const start = Date.now();
  try {
    while (Date.now() - start < timeoutMs) {
      if (existsSync(resultPath)) {
        return JSON.parse(readFileSync(resultPath, "utf-8"));
      }
      if (child.exitCode !== null) {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }

    throw new Error(
      `${flow} did not produce a result within ${timeoutMs}ms.\n` +
      `--- tauri stdout tail ---\n${tail(stdout)}\n` +
      `--- tauri stderr tail ---\n${tail(stderr)}`,
    );
  } finally {
    killTree(child);
  }
}

const seedResultPath = resolve(tempRoot, "yuanbao-tauri-session-recovery-seed-result.json");
const verifyResultPath = resolve(tempRoot, "yuanbao-tauri-session-recovery-verify-result.json");

const seedResult = await runDesktopFlow("session-recovery-seed", seedResultPath);
console.log(JSON.stringify(seedResult, null, 2));
if (!seedResult.ok) {
  process.exit(1);
}

const verifyResult = await runDesktopFlow("session-recovery-verify", verifyResultPath);
console.log(JSON.stringify(verifyResult, null, 2));

process.exit(verifyResult.ok ? 0 : 1);
