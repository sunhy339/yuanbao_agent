import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { SkillsWorkspace } from "./SkillsWorkspace";

vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("SkillsWorkspace", () => {
  it("inspects runtime skill prompt and tool allowlist details", async () => {
    const user = userEvent.setup();
    render(
      <SkillsWorkspace
        providerLabel="local"
        mcpServers={[]}
        skills={[
          {
            id: "docs",
            name: "Docs",
            description: "Document workflows",
            path: "category:productivity",
            systemPrompt: "Use document-safe editing workflows.",
            toolWhitelist: ["read_docx", "write_docx"],
            isBuiltin: true,
            enabled: true,
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: "检查" }));

    expect(screen.getByLabelText("Docs 技能详情")).toBeInTheDocument();
    expect(screen.getByText("Use document-safe editing workflows.")).toBeInTheDocument();
    expect(screen.getByText("read_docx")).toBeInTheDocument();
    expect(screen.getByText("write_docx")).toBeInTheDocument();
    expect(screen.getAllByText("内置").length).toBeGreaterThan(0);
  });

  it("keeps skill management actions limited to wired runtime actions", async () => {
    const user = userEvent.setup();
    const onRefreshSkills = vi.fn();
    const onOpenMcp = vi.fn();
    const onOpenSettings = vi.fn();
    render(
      <SkillsWorkspace
        providerLabel="local"
        mcpServers={[]}
        skills={[]}
        onRefreshSkills={onRefreshSkills}
        onOpenMcp={onOpenMcp}
        onOpenSettings={onOpenSettings}
      />,
    );

    await user.click(screen.getByRole("button", { name: "刷新技能" }));
    await user.click(screen.getByRole("button", { name: "管理 MCP" }));
    await user.click(screen.getByRole("button", { name: "运行时设置" }));

    expect(onRefreshSkills).toHaveBeenCalledTimes(1);
    expect(onOpenMcp).toHaveBeenCalledTimes(1);
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /添加技能/ })).not.toBeInTheDocument();
  });

  it("imports skill packages, folders, and opens the skills directory through callbacks", async () => {
    const user = userEvent.setup();
    const onImportSkills = vi.fn().mockResolvedValue(undefined);
    const onOpenSkillsFolder = vi.fn();
    const openDialogMock = vi.mocked(openDialog);
    openDialogMock
      .mockResolvedValueOnce("D:\\skills\\reviewer.zip")
      .mockResolvedValueOnce(["D:\\skills\\researcher"])
      .mockResolvedValueOnce(null);

    render(
      <SkillsWorkspace
        providerLabel="local"
        mcpServers={[]}
        skills={[]}
        onImportSkills={onImportSkills}
        onOpenSkillsFolder={onOpenSkillsFolder}
      />,
    );

    await user.click(screen.getByRole("button", { name: "导入 JSON/ZIP" }));
    await user.click(screen.getByRole("button", { name: "导入文件夹" }));
    await user.click(screen.getByRole("button", { name: "导入 JSON/ZIP" }));
    await user.click(screen.getByRole("button", { name: "打开目录" }));

    expect(openDialogMock).toHaveBeenNthCalledWith(1, {
      multiple: false,
      filters: [{ name: "Skill package", extensions: ["json", "zip"] }],
    });
    expect(openDialogMock).toHaveBeenNthCalledWith(2, {
      directory: true,
      multiple: false,
    });
    expect(onImportSkills).toHaveBeenCalledTimes(2);
    expect(onImportSkills).toHaveBeenNthCalledWith(1, "D:\\skills\\reviewer.zip");
    expect(onImportSkills).toHaveBeenNthCalledWith(2, "D:\\skills\\researcher");
    expect(onOpenSkillsFolder).toHaveBeenCalledTimes(1);
  });

  it("filters installed skills by search text and type", async () => {
    const user = userEvent.setup();
    render(
      <SkillsWorkspace
        providerLabel="local"
        mcpServers={[]}
        skills={[
          {
            id: "docs",
            name: "Docs",
            description: "Document workflows",
            path: "category:productivity",
            systemPrompt: "Use document-safe editing workflows.",
            toolWhitelist: ["read_docx", "write_docx"],
            isBuiltin: true,
            enabled: true,
          },
          {
            id: "custom-reviewer",
            name: "Custom Reviewer",
            description: "Checks implementation plans",
            path: "category:custom",
            systemPrompt: "Review the proposed change.",
            toolWhitelist: ["read_file"],
            isBuiltin: false,
            enabled: true,
          },
        ]}
      />,
    );

    await user.type(screen.getByRole("textbox", { name: "搜索技能" }), "review");

    expect(screen.queryByText("Docs")).not.toBeInTheDocument();
    expect(screen.getByText("Custom Reviewer")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "内置" }));

    expect(screen.queryByText("Custom Reviewer")).not.toBeInTheDocument();
    expect(screen.getByText("没有匹配的技能")).toBeInTheDocument();
  });

  it("creates, edits, and deletes custom skill presets through callbacks", async () => {
    const user = userEvent.setup();
    const onCreateSkill = vi.fn().mockResolvedValue(undefined);
    const onUpdateSkill = vi.fn().mockResolvedValue(undefined);
    const onDeleteSkill = vi.fn().mockResolvedValue(undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <SkillsWorkspace
        providerLabel="local"
        mcpServers={[]}
        skills={[
          {
            id: "custom-reviewer",
            name: "Custom Reviewer",
            description: "Checks implementation plans",
            path: "category:custom",
            systemPrompt: "Review the proposed change.",
            toolWhitelist: ["read_file"],
            isBuiltin: false,
            enabled: true,
          },
        ]}
        onCreateSkill={onCreateSkill}
        onUpdateSkill={onUpdateSkill}
        onDeleteSkill={onDeleteSkill}
      />,
    );

    await user.click(screen.getByRole("button", { name: "新建自定义技能" }));
    await user.type(screen.getByLabelText("名称"), "Research Reviewer");
    await user.clear(screen.getByLabelText("分类"));
    await user.type(screen.getByLabelText("分类"), "research");
    await user.type(screen.getByLabelText("系统提示词"), "Check every claim against sources.");
    await user.type(screen.getByLabelText("工具白名单"), "web_search\nread_file");
    await user.click(screen.getByRole("button", { name: "创建技能" }));

    expect(onCreateSkill).toHaveBeenCalledWith({
      name: "Research Reviewer",
      description: "",
      systemPrompt: "Check every claim against sources.",
      toolWhitelist: "web_search\nread_file",
      category: "research",
    });

    await user.click(screen.getByRole("button", { name: "检查" }));
    await user.click(screen.getByRole("button", { name: "编辑" }));
    await user.clear(screen.getByLabelText("名称"));
    await user.type(screen.getByLabelText("名称"), "Custom Reviewer Updated");
    await user.click(screen.getByRole("button", { name: "保存技能" }));

    expect(onUpdateSkill).toHaveBeenCalledWith(
      "custom-reviewer",
      expect.objectContaining({
        name: "Custom Reviewer Updated",
        systemPrompt: "Review the proposed change.",
      }),
    );

    await user.click(screen.getByRole("button", { name: "删除" }));

    expect(confirm).toHaveBeenCalledWith("删除技能“Custom Reviewer”？");
    expect(onDeleteSkill).toHaveBeenCalledWith("custom-reviewer");
  });
});
