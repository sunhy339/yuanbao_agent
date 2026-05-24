import { describe, expect, it } from "vitest";
import { getTaskPhase, hasBlockingTaskFailure } from "./taskPhase";

describe("taskPhase", () => {
  it("keeps a completed task in failed phase when the latest verification still failed", () => {
    const task = {
      id: "task_1",
      status: "completed",
      verification: [
        {
          id: "verify_1",
          command: "python -m py_compile snake_game/*.py",
          status: "failed",
        },
      ],
    };

    expect(hasBlockingTaskFailure(task)).toBe(true);
    expect(getTaskPhase(task)).toBe("failed");
  });

  it("clears an earlier failed verification when the same command later passes", () => {
    const task = {
      id: "task_1",
      status: "completed",
      verification: [
        {
          id: "verify_1",
          command: "python -m pytest -q",
          status: "failed",
        },
        {
          id: "verify_2",
          command: "python -m pytest -q",
          status: "passed",
        },
      ],
    };

    expect(hasBlockingTaskFailure(task)).toBe(false);
    expect(getTaskPhase(task)).toBe("completed");
  });
});
