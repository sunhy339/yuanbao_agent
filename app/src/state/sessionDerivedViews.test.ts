import { describe, expect, it } from "vitest";
import type { TraceEventRecord } from "@shared";
import { buildSessionCollaboration } from "./sessionDerivedViews";

describe("buildSessionCollaboration", () => {
  it("surfaces structured child task attention signals", () => {
    const collaboration = buildSessionCollaboration(
      [],
      [
        {
          id: "trace_child_completed",
          sessionId: "sess_1",
          taskId: "child_1",
          type: "collab.task.completed",
          source: "runtime",
          sequence: 1,
          createdAt: 1778734169000,
          payload: {
            task: {
              id: "child_1",
              title: "Validate frontend",
              status: "completed",
              result: {
                summary: "Validation status: partial, with one blocker.",
                validationStatus: "partial",
              },
            },
          },
        } satisfies TraceEventRecord,
      ],
    );

    expect(collaboration.childTasks?.[0]?.status).toBe("completed");
    expect(collaboration.childTasks?.[0]?.attention).toContain("Validation status: partial");
  });
});
