import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import net from "node:net";

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

async function runDesktopFlow(flow, resultPath, devPort) {
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
  const tauriConfigPath = resolve(
    process.env.YUANBAO_TAURI_E2E_TAURI_CONFIG_PATH ||
      `${tempRoot}/yuanbao-tauri-session-recovery-${flow}-${devPort}.json`,
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

const seedDevPort = await findFreePort(process.env.YUANBAO_TAURI_E2E_DEV_PORT);
const seedResult = await runDesktopFlow("session-recovery-seed", seedResultPath, seedDevPort);
console.log(JSON.stringify(seedResult, null, 2));
if (!seedResult.ok) {
  process.exit(1);
}

const verifyDevPort = await findFreePort(process.env.YUANBAO_TAURI_E2E_DEV_PORT);
const verifyResult = await runDesktopFlow("session-recovery-verify", verifyResultPath, verifyDevPort);
console.log(JSON.stringify(verifyResult, null, 2));

process.exit(verifyResult.ok ? 0 : 1);
