import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SkillsWorkspace } from "./SkillsWorkspace";

afterEach(() => {
  cleanup();
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

    await user.click(screen.getByRole("button", { name: "Inspect" }));

    expect(screen.getByLabelText("Docs skill details")).toBeInTheDocument();
    expect(screen.getByText("Use document-safe editing workflows.")).toBeInTheDocument();
    expect(screen.getByText("read_docx")).toBeInTheDocument();
    expect(screen.getByText("write_docx")).toBeInTheDocument();
    expect(screen.getAllByText("built-in").length).toBeGreaterThan(0);
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

    await user.click(screen.getByRole("button", { name: "Refresh skills" }));
    await user.click(screen.getByRole("button", { name: "Manage MCP" }));
    await user.click(screen.getByRole("button", { name: "Runtime settings" }));

    expect(onRefreshSkills).toHaveBeenCalledTimes(1);
    expect(onOpenMcp).toHaveBeenCalledTimes(1);
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /Add skill/i })).not.toBeInTheDocument();
  });
});
