import { describe, expect, it, vi } from "vitest";
import {
  buildPromptAttachmentsWithReferences,
  extractPromptFileReferences,
  resolveWorkspaceForSlashCommand,
} from "./useMessageActions";

describe("prompt file references", () => {
  it("extracts unique @ file references from prompt text", () => {
    expect(
      extractPromptFileReferences("请看 @app/src/App.tsx 和 @docs/readme.md，重复 @app/src/App.tsx"),
    ).toEqual(["app/src/App.tsx", "docs/readme.md"]);
  });

  it("normalizes backslashes and merges references into attachments", () => {
    expect(
      buildPromptAttachmentsWithReferences("继续 @app\\src\\ui\\haha-clean\\TODO.md。", [
        "manual.png",
        "app/src/ui/haha-clean/TODO.md",
      ]),
    ).toEqual({
      attachments: ["manual.png", "app/src/ui/haha-clean/TODO.md"],
      fileReferences: ["app/src/ui/haha-clean/TODO.md"],
    });
  });

  it("resolves slash commands against the launch workspace path", async () => {
    const ensureWorkspace = vi.fn(async () => ({ id: "ws_yuanbao", rootPath: "D:/py/yuanbao_agent" }));
    const openWorkspaceAtPath = vi.fn(async (path: string) => ({ id: "ws_snake", rootPath: path }));

    await expect(resolveWorkspaceForSlashCommand({
      launch: { workDir: "D:/py/snake_game" },
      workspace: { id: "ws_yuanbao", rootPath: "D:/py/yuanbao_agent" },
      ensureWorkspace,
      openWorkspaceAtPath,
    })).resolves.toEqual({ id: "ws_snake", rootPath: "D:/py/snake_game" });

    expect(openWorkspaceAtPath).toHaveBeenCalledWith("D:/py/snake_game");
    expect(ensureWorkspace).not.toHaveBeenCalled();
  });
});
