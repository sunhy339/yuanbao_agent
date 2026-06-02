import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { initializeMock, renderMock, sanitizeMock } = vi.hoisted(() => ({
  initializeMock: vi.fn(),
  renderMock: vi.fn(),
  sanitizeMock: vi.fn((value: string) => value),
}));

vi.mock("mermaid", () => ({
  default: {
    initialize: initializeMock,
    render: renderMock,
  },
}));

vi.mock("dompurify", () => ({
  default: {
    sanitize: sanitizeMock,
  },
}));

import { CleanMarkdown } from "./CleanMarkdown";

afterEach(() => cleanup());

describe("CleanMarkdown", () => {
  beforeEach(() => {
    initializeMock.mockClear();
    sanitizeMock.mockClear();
    renderMock.mockReset();
    renderMock.mockResolvedValue({
      svg: '<svg viewBox="0 0 120 60"><text>diagram</text></svg>',
    });
  });

  it("renders loose markdown headings without exposing raw hashes", () => {
    render(<CleanMarkdown content={"##1 项目结构\n\n### 这次改了哪些文件"} />);

    expect(screen.getByRole("heading", { name: "1 项目结构" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "这次改了哪些文件" })).toBeTruthy();
    expect(screen.queryByText("##1 项目结构")).toBeNull();
  });

  it("splits compact assistant markdown into readable blocks", () => {
    const { container } = render(
      <CleanMarkdown
        content={"Done. ### Changes - `game.py`: loop update - `rules.py`: rules update 1. Run tests 2. Update docs"}
      />,
    );

    expect(screen.getByRole("heading", { name: "Changes" })).toBeTruthy();
    expect(screen.getByText("game.py")).toBeTruthy();
    expect(screen.getByText("rules.py")).toBeTruthy();
    expect(container.querySelector("ul")).toBeTruthy();
    expect(container.querySelector("ol")).toBeTruthy();
  });

  it("renders task lists and simple markdown tables", () => {
    render(
      <CleanMarkdown
        content={[
          "- [x] 读取文件",
          "- [ ] 运行测试",
          "",
          "| 文件 | 状态 |",
          "| --- | --- |",
          "| `game.py` | 修改 |",
        ].join("\n")}
      />,
    );

    expect(screen.getByText("读取文件")).toBeTruthy();
    expect(screen.getByRole("checkbox", { checked: true })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "文件" })).toBeTruthy();
    expect(screen.getByText("game.py")).toBeTruthy();
  });

  it("renders nested mixed markdown lists without flattening structure", () => {
    const { container } = render(
      <CleanMarkdown
        content={[
          "- `game.py`",
          "  - 主循环",
          "  - 碰撞处理",
          "    1. 越界",
          "    2. 自碰撞",
          "- [ ] 补测试",
        ].join("\n")}
      />,
    );

    expect(screen.getByText("game.py")).toBeTruthy();
    expect(screen.getByText("主循环")).toBeTruthy();
    expect(screen.getByText("越界")).toBeTruthy();
    expect(screen.getByRole("checkbox", { checked: false })).toBeTruthy();
    expect(container.querySelector("li li li")).toBeTruthy();
  });

  it("labels fenced code copy actions by language", () => {
    render(<CleanMarkdown content={"```python\nprint('hi')\n```"} />);

    expect(screen.getByText("python")).toBeTruthy();
    expect(screen.getByRole("button", { name: "复制 python 代码块" })).toBeTruthy();
  });

  it("highlights diff and json code blocks with semantic token classes", () => {
    const { container } = render(
      <CleanMarkdown
        content={[
          "```diff",
          "@@ -1,2 +1,2 @@",
          "-old_value",
          "+new_value",
          "```",
          "",
          "```json",
          "{\"status\": \"ok\", \"count\": 2, \"enabled\": true}",
          "```",
        ].join("\n")}
      />,
    );

    expect(container.querySelector(".hc-code-line-hunk")).toBeTruthy();
    expect(container.querySelector(".hc-code-line-deleted")).toBeTruthy();
    expect(container.querySelector(".hc-code-line-added")).toBeTruthy();
    expect(container.querySelector(".hc-code-property")?.textContent).toBe('"status"');
    expect(container.querySelector(".hc-code-number")?.textContent).toBe("2");
    expect(container.querySelector(".hc-code-builtin")?.textContent).toBe("true");
  });

  it("renders mermaid fenced blocks as diagrams", async () => {
    render(<CleanMarkdown content={"```mermaid\ngraph TB\nA-->B\n```"} />);

    await screen.findByLabelText("打开 Mermaid 图表预览");
    await waitFor(() => {
      expect(renderMock).toHaveBeenCalledWith(expect.stringMatching(/^hc-mermaid-/), "graph TB\nA-->B");
    });
    expect(screen.getByText("Mermaid")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "复制 mermaid 代码块" })).toBeNull();
  });

  it("detects unlabeled mermaid diagrams from the first meaningful line", async () => {
    render(<CleanMarkdown content={"```\nflowchart LR\nA-->B\n```"} />);

    await waitFor(() => {
      expect(renderMock).toHaveBeenCalledWith(expect.stringMatching(/^hc-mermaid-/), "flowchart LR\nA-->B");
    });
    expect(screen.getByLabelText("打开 Mermaid 图表预览")).toBeTruthy();
  });

  it("renders safe markdown images as bounded image blocks", () => {
    render(<CleanMarkdown content={"![UI screenshot](D:\\tmp\\preview.png)"} />);

    expect(screen.getByRole("img", { name: "UI screenshot" }).getAttribute("src")).toBe("D:/tmp/preview.png");
    expect(screen.getByText("UI screenshot")).toBeTruthy();
  });
});
