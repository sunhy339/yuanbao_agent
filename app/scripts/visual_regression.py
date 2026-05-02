from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
OUTPUT_ROOT = REPO_ROOT / "docs" / "frontend-v2-visual-regression"
BASE_URL = os.environ.get("VISUAL_BASE_URL", "http://127.0.0.1:5174")
THEME_VARIANTS = [
    theme.strip()
    for theme in os.environ.get("VISUAL_THEMES", "dark,light").split(",")
    if theme.strip() in {"dark", "light"}
]

VIEWPORTS = [
    {"id": "desktop", "width": 1440, "height": 1000, "is_mobile": False},
]

PAGES = [
    {"id": "overview", "label": "Overview", "action": "overview"},
    {"id": "new-session", "label": "New Session", "action": "new-session"},
    {"id": "chat", "label": "Chat", "action": "chat"},
    {"id": "settings", "label": "Settings", "action": "settings"},
    {"id": "scheduled", "label": "Scheduled", "action": "scheduled"},
    {"id": "mcp", "label": "MCP", "action": "mcp"},
    {"id": "skills", "label": "Agent Skills", "action": "skills"},
    {"id": "appearance", "label": "Appearance", "action": "appearance"},
    {"id": "playground", "label": "Component Playground", "action": "playground"},
]


def wait_for_app(page: Page) -> None:
    page.goto(BASE_URL, wait_until="networkidle", timeout=30_000)
    page.locator(".yb-app-shell").wait_for(state="visible", timeout=20_000)
    page.wait_for_timeout(350)


def force_theme(page: Page, theme: str) -> None:
    page.evaluate(
        """(theme) => {
          const root = document.querySelector(".yb-v2");
          if (root) {
            root.setAttribute("data-theme", theme);
          }
        }""",
        theme,
    )
    page.wait_for_timeout(100)


def click_by_aria(page: Page, label: str) -> None:
    target = page.locator(f'button[aria-label="{label}"]')
    target.wait_for(state="visible", timeout=10_000)
    target.click()
    page.wait_for_timeout(500)


def open_page(page: Page, action: str) -> None:
    if action == "overview":
        return
    if action == "new-session":
        click_by_aria(page, "New Session")
        return
    if action == "settings":
        click_by_aria(page, "Settings")
        return
    if action == "scheduled":
        click_by_aria(page, "Scheduled")
        return
    if action == "mcp":
        click_by_aria(page, "MCP Center")
        return
    if action == "skills":
        click_by_aria(page, "Agent Skills")
        return
    if action == "appearance":
        click_by_aria(page, "Appearance")
        return
    if action == "playground":
        click_by_aria(page, "Component Playground")
        return
    if action == "chat":
        session_items = page.locator(".session-rail .session-rail-item")
        if session_items.count() > 0:
            session_items.first.click()
            page.wait_for_timeout(700)
            return
        click_by_aria(page, "New Session")
        create_button = page.locator("button").filter(has_text="Create session")
        create_button.wait_for(state="visible", timeout=10_000)
        create_button.click()
        page.wait_for_timeout(1_000)
        return
    raise ValueError(f"Unknown action: {action}")


def collect_diagnostics(page: Page) -> dict:
    return page.evaluate(
        """() => {
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
          const inManagedHorizontalScroller = (el) => {
            let parent = el.parentElement;
            while (parent && parent !== document.body) {
              const style = getComputedStyle(parent);
              const rect = parent.getBoundingClientRect();
              const scrolls = parent.scrollWidth > parent.clientWidth + 2;
              const clipsX = ["auto", "scroll", "hidden"].includes(style.overflowX);
              if (scrolls && clipsX && rect.left >= -2 && rect.right <= vw + 2) {
                return true;
              }
              parent = parent.parentElement;
            }
            return false;
          };
          const textOf = (el, max = 100) => (el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, max);
          const offenders = [...document.querySelectorAll("body *")]
            .filter((el) => visible(el))
            .map((el) => {
              const rect = el.getBoundingClientRect();
              return {
                tag: el.tagName.toLowerCase(),
                className: String(el.className || "").slice(0, 90),
                text: textOf(el),
                left: Math.round(rect.left),
                right: Math.round(rect.right),
                width: Math.round(rect.width),
                managedHorizontal: inManagedHorizontalScroller(el)
              };
            })
            .filter((item) => !item.managedHorizontal && (item.left < -2 || item.right > vw + 2))
            .slice(0, 20);
          const clippedButtons = [...document.querySelectorAll("button")]
            .filter((button) => visible(button) && (button.scrollWidth > button.clientWidth + 1 || button.scrollHeight > button.clientHeight + 1))
            .map((button) => ({
              label: button.getAttribute("aria-label") || textOf(button, 80),
              className: String(button.className || ""),
              scrollWidth: button.scrollWidth,
              clientWidth: button.clientWidth,
              scrollHeight: button.scrollHeight,
              clientHeight: button.clientHeight
            }))
            .slice(0, 20);
          const nestedPanels = [...document.querySelectorAll(".yb-panel .yb-panel, .settings-provider-detail .settings-provider-detail")]
            .map((el) => textOf(el, 100));
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
        }"""
    )


def write_report(results: list[dict]) -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    lines = [
        "# Frontend V2 Visual Regression",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Base URL: {BASE_URL}",
        "",
        "| Theme | Viewport | Page | Screenshot | Horizontal overflow | Clipped buttons | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for item in results:
        shot = Path(item["screenshot"])
        notes = []
        if item["diagnostics"]["offenders"]:
            notes.append(f"{len(item['diagnostics']['offenders'])} overflow offenders")
        if item["diagnostics"]["nestedPanels"]:
            notes.append(f"{len(item['diagnostics']['nestedPanels'])} nested panels")
        lines.append(
            "| {theme} | {viewport} | {page} | [png]({screenshot}) | {overflow} | {clipped} | {notes} |".format(
                theme=item.get("theme", "dark"),
                viewport=item["viewport"],
                page=item["page"],
                screenshot=shot.name,
                overflow="YES" if item["diagnostics"]["overflowX"] else "no",
                clipped=len(item["diagnostics"]["clippedButtons"]),
                notes="; ".join(notes) if notes else "OK",
            )
        )

    issues = []
    for item in results:
        prefix = f"{item.get('theme', 'dark')}/{item['viewport']}/{item['page']}"
        for offender in item["diagnostics"]["offenders"]:
            issues.append(
                f"- {prefix}: overflow {offender['tag']}.{offender['className']} "
                f"\"{offender['text']}\" right={offender['right']}"
            )
        for button in item["diagnostics"]["clippedButtons"]:
            issues.append(
                f"- {prefix}: clipped button \"{button['label']}\" "
                f"{button['clientWidth']}x{button['clientHeight']} "
                f"scroll {button['scrollWidth']}x{button['scrollHeight']}"
            )
        for panel in item["diagnostics"]["nestedPanels"]:
            issues.append(f"- {prefix}: nested panel \"{panel}\"")

    lines.extend(["", "## Findings", ""])
    lines.extend(issues if issues else ["No automated overflow, clipped-button, or nested-panel findings."])
    lines.append("")
    (OUTPUT_ROOT / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for viewport in VIEWPORTS:
                context = browser.new_context(
                    viewport={"width": viewport["width"], "height": viewport["height"]},
                    is_mobile=viewport["is_mobile"],
                    device_scale_factor=1,
                )
                page = context.new_page()
                try:
                    for theme in THEME_VARIANTS:
                        for page_config in PAGES:
                            wait_for_app(page)
                            force_theme(page, theme)
                            open_page(page, page_config["action"])
                            force_theme(page, theme)
                            diagnostics = collect_diagnostics(page)
                            screenshot_name = (
                                f"{viewport['id']}-{page_config['id']}.png"
                                if theme == "dark"
                                else f"{theme}-{viewport['id']}-{page_config['id']}.png"
                            )
                            screenshot = OUTPUT_ROOT / screenshot_name
                            page.screenshot(path=str(screenshot), full_page=True)
                            results.append(
                                {
                                    "theme": theme,
                                    "viewport": viewport["id"],
                                    "page": page_config["label"],
                                    "screenshot": str(screenshot),
                                    "diagnostics": diagnostics,
                                }
                            )
                finally:
                    context.close()
        finally:
            browser.close()

    write_report(results)
    issue_count = sum(
        len(item["diagnostics"]["offenders"])
        + len(item["diagnostics"]["clippedButtons"])
        + len(item["diagnostics"]["nestedPanels"])
        for item in results
    )
    print(f"Visual regression complete: {len(results)} screenshots, {issue_count} automated findings.")
    print(OUTPUT_ROOT / "README.md")
    return 2 if issue_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
