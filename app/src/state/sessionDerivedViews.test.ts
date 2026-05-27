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

  it("keeps child tasks in creation order instead of latest-completed order", () => {
    const collaboration = buildSessionCollaboration(
      [],
      [
        {
          id: "trace_first",
          sessionId: "sess_1",
          taskId: "child_1",
          type: "collab.task.completed",
          source: "runtime",
          sequence: 1,
          createdAt: 1000,
          payload: {
            task: {
              id: "child_1",
              title: "Inspect README and source files",
              status: "completed",
              createdAt: 100,
              updatedAt: 400,
            },
          },
        } satisfies TraceEventRecord,
        {
          id: "trace_second",
          sessionId: "sess_1",
          taskId: "child_2",
          type: "collab.task.completed",
          source: "runtime",
          sequence: 2,
          createdAt: 1100,
          payload: {
            task: {
              id: "child_2",
              title: "Run the pytest suite",
              status: "completed",
              createdAt: 200,
              updatedAt: 300,
            },
          },
        } satisfies TraceEventRecord,
      ],
    );

    expect(collaboration.childTasks?.map((task) => task.title)).toEqual([
      "Inspect README and source files",
      "Run the pytest suite",
    ]);
  });
});
