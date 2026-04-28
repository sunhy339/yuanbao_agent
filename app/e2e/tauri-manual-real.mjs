import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const appRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const tempRoot = process.env.TEMP || process.env.TMP || appRoot;
const databasePath = resolve(
  process.env.LOCAL_AGENT_DB_PATH ||
    process.env.YUANBAO_TAURI_MANUAL_DB_PATH ||
    `${tempRoot}/yuanbao-tauri-manual-real.sqlite3`,
);

mkdirSync(dirname(databasePath), { recursive: true });

const env = {
  ...process.env,
  LOCAL_AGENT_DB_PATH: databasePath,
};

console.log("Starting real Tauri desktop manual session.");
console.log(`Runtime database: ${databasePath}`);
console.log("Configure a real provider in Settings, then create/reopen sessions to verify persisted messages.");

const command = process.platform === "win32" ? "cmd.exe" : "npm";
const args = process.platform === "win32"
  ? ["/d", "/s", "/c", "npm run tauri:dev"]
  : ["run", "tauri:dev"];

const child = spawn(command, args, {
  cwd: appRoot,
  env,
  stdio: "inherit",
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
