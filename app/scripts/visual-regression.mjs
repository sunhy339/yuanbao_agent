import { spawn } from "node:child_process";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = resolve(appRoot, "..");
const outputRoot = resolve(repoRoot, "docs", "frontend-v2-visual-regression");
const baseUrl = process.env.VISUAL_BASE_URL ?? "http://127.0.0.1:5174";
const chromePath = process.env.CHROME_PATH ?? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const debugPort = Number(process.env.CHROME_DEBUG_PORT ?? "9224");

const pages = [
  { id: "overview", label: "Overview", action: async () => undefined },
  { id: "new-session", label: "New Session", action: async (cdp) => clickAria(cdp, "New Session") },
  { id: "chat", label: "Chat", action: async (cdp) => clickFirstSession(cdp) },
  { id: "settings", label: "Settings", action: async (cdp) => clickAria(cdp, "Settings") },
  { id: "scheduled", label: "Scheduled", action: async (cdp) => clickAria(cdp, "Scheduled") },
  { id: "mcp", label: "MCP", action: async (cdp) => clickAria(cdp, "MCP Center") },
];

const viewports = [
  { id: "desktop", width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false },
  { id: "mobile", width: 390, height: 844, deviceScaleFactor: 1, mobile: true },
];

class CdpClient {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.nextId = 1;
    this.pending = new Map();
    this.events = new Map();
  }

  async connect() {
    this.ws = new WebSocket(this.wsUrl);
    await new Promise((resolveOpen, rejectOpen) => {
      this.ws.addEventListener("open", resolveOpen, { once: true });
      this.ws.addEventListener("error", rejectOpen, { once: true });
    });
    this.ws.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve: resolvePending, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) {
          reject(new Error(`${message.error.message}: ${message.error.data ?? ""}`));
        } else {
          resolvePending(message.result ?? {});
        }
        return;
      }
      if (message.method) {
        const listeners = this.events.get(message.method) ?? [];
        this.events.set(message.method, []);
        listeners.forEach((listener) => listener(message.params ?? {}));
      }
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolvePending, reject) => {
      this.pending.set(id, { resolve: resolvePending, reject });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`Timed out waiting for ${method}`));
        }
      }, 15_000);
    });
  }

  waitForEvent(method, timeoutMs = 15_000) {
    return new Promise((resolvePending, reject) => {
      const timer = setTimeout(() => reject(new Error(`Timed out waiting for ${method}`)), timeoutMs);
      const listeners = this.events.get(method) ?? [];
      listeners.push((params) => {
        clearTimeout(timer);
        resolvePending(params);
      });
      this.events.set(method, listeners);
    });
  }

  close() {
    this.ws?.close();
  }
}

async function waitForJson(url, attempts = 50) {
  let lastError;
  for (let index = 0; index < attempts; index += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return response.json();
      }
    } catch (error) {
      lastError = error;
    }
    await delay(200);
  }
  throw lastError ?? new Error(`Unable to fetch ${url}`);
}

function delay(ms) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, ms));
}

async function launchChrome() {
  const userDataDir = resolve(tmpdir(), `yuanbao-visual-${Date.now()}`);
  const chrome = spawn(chromePath, [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    `--remote-debugging-port=${debugPort}`,
    `--user-data-dir=${userDataDir}`,
    "--window-size=1440,1000",
    "about:blank",
  ], {
    stdio: "ignore",
  });

  chrome.on("exit", () => {
    void rm(userDataDir, { recursive: true, force: true }).catch(() => undefined);
  });

  await waitForJson(`http://127.0.0.1:${debugPort}/json/version`);
  const targets = await waitForJson(`http://127.0.0.1:${debugPort}/json/list`);
  const target = targets.find((item) => item.type === "page" && item.webSocketDebuggerUrl);
  if (!target) {
    throw new Error("No page target exposed by Chrome.");
  }

  return { chrome, wsUrl: target.webSocketDebuggerUrl };
}

async function setupPage(cdp, viewport) {
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: viewport.deviceScaleFactor,
    mobile: viewport.mobile,
  });
}

async function navigate(cdp) {
  const loaded = cdp.waitForEvent("Page.loadEventFired").catch(() => undefined);
  await cdp.send("Page.navigate", { url: baseUrl });
  await loaded;
  await waitForApp(cdp);
}

async function waitForApp(cdp) {
  for (let index = 0; index < 60; index += 1) {
    const result = await evaluate(cdp, `
      Boolean(document.querySelector(".yb-app-shell")) &&
      !document.body.textContent.includes("Loading workbench")
    `);
    if (result === true) {
      await delay(350);
      return;
    }
    await delay(250);
  }
  throw new Error("App shell did not become ready.");
}

async function evaluate(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text ?? "Runtime.evaluate failed");
  }
  return result.result?.value;
}

async function clickAria(cdp, ariaLabel) {
  const clicked = await evaluate(cdp, `
    (() => {
      const target = [...document.querySelectorAll("button")]
        .find((button) => button.getAttribute("aria-label") === ${JSON.stringify(ariaLabel)});
      if (!target) return false;
      target.click();
      return true;
    })()
  `);
  if (!clicked) {
    throw new Error(`Button not found: ${ariaLabel}`);
  }
  await delay(350);
}

async function clickFirstSession(cdp) {
  let clicked = await evaluate(cdp, `
    (() => {
      const target = document.querySelector(".session-rail .session-rail-item");
      if (!target) return false;
      target.click();
      return true;
    })()
  `);
  if (!clicked) {
    await clickAria(cdp, "New Session");
    clicked = await evaluate(cdp, `
      (() => {
        const button = [...document.querySelectorAll("button")]
          .find((item) => item.textContent.includes("Create session"));
        if (!button) return false;
        button.click();
        return true;
      })()
    `);
  }
  if (!clicked) {
    throw new Error("No session available and create session button was not found.");
  }
  await delay(600);
}

async function capture(cdp, filepath, viewport) {
  const metrics = await cdp.send("Page.getLayoutMetrics");
  const contentSize = metrics.contentSize ?? { width: viewport.width, height: viewport.height };
  const width = Math.ceil(Math.max(contentSize.width, viewport.width));
  const height = Math.ceil(Math.min(Math.max(contentSize.height, viewport.height), 4200));
  const screenshot = await cdp.send("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
    captureBeyondViewport: true,
    clip: { x: 0, y: 0, width, height, scale: 1 },
  });
  await mkdir(dirname(filepath), { recursive: true });
  await writeFile(filepath, Buffer.from(screenshot.data, "base64"));
}

async function collectDiagnostics(cdp) {
  return evaluate(cdp, `
    (() => {
      const vw = window.innerWidth;
      const root = document.documentElement;
      const body = document.body;
      const scrollWidth = Math.max(root.scrollWidth, body.scrollWidth);
      const overflowX = scrollWidth > vw + 2;
      const visible = (el) => {
        const style = getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
      };
      const offenders = [...document.querySelectorAll("body *")]
        .filter((el) => visible(el))
        .map((el) => {
          const rect = el.getBoundingClientRect();
          return {
            tag: el.tagName.toLowerCase(),
            className: String(el.className || "").slice(0, 90),
            text: (el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 100),
            left: Math.round(rect.left),
            right: Math.round(rect.right),
            width: Math.round(rect.width)
          };
        })
        .filter((item) => item.left < -2 || item.right > vw + 2)
        .slice(0, 16);
      const clippedButtons = [...document.querySelectorAll("button")]
        .filter((button) => visible(button) && (button.scrollWidth > button.clientWidth + 1 || button.scrollHeight > button.clientHeight + 1))
        .map((button) => ({
          label: button.getAttribute("aria-label") || button.textContent.replace(/\\s+/g, " ").trim(),
          className: String(button.className || ""),
          scrollWidth: button.scrollWidth,
          clientWidth: button.clientWidth,
          scrollHeight: button.scrollHeight,
          clientHeight: button.clientHeight
        }))
        .slice(0, 16);
      const nestedPanels = [...document.querySelectorAll(".yb-panel .yb-panel, .settings-provider-detail .settings-provider-detail")]
        .map((el) => (el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 80));
      return {
        title: document.title,
        viewport: { width: vw, height: window.innerHeight },
        scrollWidth,
        overflowX,
        offenders,
        clippedButtons,
        nestedPanels,
        appReady: Boolean(document.querySelector(".yb-app-shell")),
      };
    })()
  `);
}

function markdown(results) {
  const lines = [
    "# Frontend V2 Visual Regression",
    "",
    `Generated: ${new Date().toISOString()}`,
    `Base URL: ${baseUrl}`,
    "",
    "| Viewport | Page | Screenshot | Horizontal overflow | Clipped buttons | Notes |",
    "| --- | --- | --- | --- | --- | --- |",
  ];

  for (const item of results) {
    const screenshot = item.screenshot.replaceAll("\\", "/").replace(resolve(repoRoot).replaceAll("\\", "/") + "/", "");
    const notes = [
      item.diagnostics.offenders.length ? `${item.diagnostics.offenders.length} overflow offenders` : "",
      item.diagnostics.nestedPanels.length ? `${item.diagnostics.nestedPanels.length} nested panels` : "",
    ].filter(Boolean).join("; ") || "OK";
    lines.push(
      `| ${item.viewport} | ${item.page} | [png](../${screenshot}) | ${item.diagnostics.overflowX ? "YES" : "no"} | ${item.diagnostics.clippedButtons.length} | ${notes} |`,
    );
  }

  const issues = results.flatMap((item) => {
    const prefix = `${item.viewport}/${item.page}`;
    return [
      ...item.diagnostics.offenders.map((offender) => `- ${prefix}: overflow ${offender.tag}.${offender.className} "${offender.text}" right=${offender.right}`),
      ...item.diagnostics.clippedButtons.map((button) => `- ${prefix}: clipped button "${button.label}" ${button.clientWidth}x${button.clientHeight} scroll ${button.scrollWidth}x${button.scrollHeight}`),
      ...item.diagnostics.nestedPanels.map((panel) => `- ${prefix}: nested panel "${panel}"`),
    ];
  });

  lines.push("", "## Findings", "");
  lines.push(...(issues.length ? issues : ["No automated overflow, clipped-button, or nested-panel findings."]));
  lines.push("");
  return lines.join("\n");
}

async function main() {
  await mkdir(outputRoot, { recursive: true });
  const { chrome, wsUrl } = await launchChrome();
  const cdp = new CdpClient(wsUrl);
  const results = [];

  try {
    await cdp.connect();
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");

    for (const viewport of viewports) {
      await setupPage(cdp, viewport);
      for (const page of pages) {
        await navigate(cdp);
        await page.action(cdp);
        const diagnostics = await collectDiagnostics(cdp);
        const screenshot = resolve(outputRoot, `${viewport.id}-${page.id}.png`);
        await capture(cdp, screenshot, viewport);
        results.push({
          viewport: viewport.id,
          page: page.label,
          screenshot,
          diagnostics,
        });
      }
    }
  } finally {
    cdp.close();
    chrome.kill();
  }

  await writeFile(resolve(outputRoot, "results.json"), JSON.stringify(results, null, 2), "utf-8");
  await writeFile(resolve(outputRoot, "README.md"), markdown(results), "utf-8");

  const issueCount = results.reduce(
    (total, item) => total + item.diagnostics.offenders.length + item.diagnostics.clippedButtons.length + item.diagnostics.nestedPanels.length,
    0,
  );
  console.log(`Visual regression complete: ${results.length} screenshots, ${issueCount} automated findings.`);
  console.log(resolve(outputRoot, "README.md"));
  if (issueCount > 0) {
    process.exitCode = 2;
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
