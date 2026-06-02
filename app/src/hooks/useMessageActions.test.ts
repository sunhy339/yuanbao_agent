import { describe, expect, it } from "vitest";
import { buildPromptAttachmentsWithReferences, extractPromptFileReferences } from "./useMessageActions";

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
});
