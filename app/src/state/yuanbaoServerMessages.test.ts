import { describe, expect, it } from "vitest";
import type { AgentEventEnvelope } from "@shared";
import { applyYuanbaoServerMessageToChat } from "./yuanbaoServerMessages";

describe("yuanbaoServerMessages", () => {
  it("preserves typed tool result file and diff metadata for clean tool cards", () => {
    const event: AgentEventEnvelope = {
      eventId: "evt_tool_result",
      sessionId: "sess_1",
      taskId: "task_1",
      type: "tool_result",
      ts: 10,
      payload: {
        filesChanged: 1,
        changedPaths: ["src/new.ts"],
        diffText: "--- /dev/null\n+++ b/src/new.ts\n@@ -0,0 +1 @@\n+export const value = 1;",
      },
      yuanbao: {
        type: "tool_result",
        toolUseId: "write_1",
        toolName: "write_file",
        content: { status: "completed", summary: "wrote src/new.ts" },
        isError: false,
      },
    };

    const result = applyYuanbaoServerMessageToChat([], event);

    expect(result.handled).toBe(true);
    expect(result.messages[0]).toMatchObject({
      id: "tool_result:write_1",
      content: "wrote src/new.ts",
      metadata: {
        resultText: "wrote src/new.ts",
        changedPaths: ["src/new.ts"],
        filesChanged: 1,
        diffText: expect.stringContaining("+++ b/src/new.ts"),
      },
    });
    expect(result.messages[0].metadata?.resultText).not.toContain("diffText");
    expect(result.messages[0].metadata?.resultText).not.toContain("changedPaths");
  });
});
