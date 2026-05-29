import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CleanMarkdown } from "./CleanMarkdown";

afterEach(() => cleanup());

describe("CleanMarkdown", () => {
  it("renders loose markdown headings without exposing raw hashes", () => {
    render(<CleanMarkdown content={"##1 项目结构\n\n### 这次改了哪些文件"} />);

    expect(screen.getByRole("heading", { name: "1 项目结构" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "这次改了哪些文件" })).toBeTruthy();
    expect(screen.queryByText("##1 项目结构")).toBeNull();
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
});
