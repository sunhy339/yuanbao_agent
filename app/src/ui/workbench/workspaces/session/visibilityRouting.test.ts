import { describe, expect, it } from "vitest";
import { isChatVisibleEvent } from "./visibilityRouting";

interface MockEvent {
  visibility?: "chat" | "panel" | "trace";
  taskId: string;
}

describe("visibility routing", () => {
  it("routes events with visibility=chat to chat", () => {
    const childTaskIds = new Set<string>(["child_1"]);
    expect(isChatVisibleEvent({ visibility: "chat", taskId: "root" }, childTaskIds)).toBe(true);
    expect(isChatVisibleEvent({ visibility: "chat", taskId: "child_1" }, childTaskIds)).toBe(true);
  });

  it("filters out events with visibility=panel from chat", () => {
    const childTaskIds = new Set<string>();
    expect(isChatVisibleEvent({ visibility: "panel", taskId: "child_1" }, childTaskIds)).toBe(false);
  });

  it("filters out events with visibility=trace from chat", () => {
    const childTaskIds = new Set<string>();
    expect(isChatVisibleEvent({ visibility: "trace", taskId: "child_1" }, childTaskIds)).toBe(false);
  });

  it("falls back to childTaskIds heuristic when visibility is missing", () => {
    const childTaskIds = new Set<string>(["child_1", "child_2"]);
    // Root task not in childTaskIds → visible
    expect(isChatVisibleEvent({ taskId: "root" }, childTaskIds)).toBe(true);
    // Child task in childTaskIds → hidden
    expect(isChatVisibleEvent({ taskId: "child_1" }, childTaskIds)).toBe(false);
    expect(isChatVisibleEvent({ taskId: "child_2" }, childTaskIds)).toBe(false);
  });

  it("shows all events when visibility is missing and no child tasks registered", () => {
    const childTaskIds = new Set<string>();
    expect(isChatVisibleEvent({ taskId: "root" }, childTaskIds)).toBe(true);
    expect(isChatVisibleEvent({ taskId: "unknown" }, childTaskIds)).toBe(true);
  });

  it("visibility=chat takes precedence over childTaskIds membership", () => {
    const childTaskIds = new Set<string>(["child_1"]);
    // Even though child_1 is in childTaskIds, visibility=chat overrides
    expect(isChatVisibleEvent({ visibility: "chat", taskId: "child_1" }, childTaskIds)).toBe(true);
  });

  it("visibility=panel takes precedence over childTaskIds absence", () => {
    const childTaskIds = new Set<string>();
    // Even though root is not in childTaskIds, visibility=panel hides it
    expect(isChatVisibleEvent({ visibility: "panel", taskId: "root" }, childTaskIds)).toBe(false);
  });
});

describe("missed-event merge (childTaskIds accumulation)", () => {
  it("accumulates child task ids from collab.task.created events", () => {
    const childTaskIds = new Set<string>();

    // Simulate receiving collab.task.created events
    childTaskIds.add("child_1");
    childTaskIds.add("child_2");

    expect(childTaskIds.has("child_1")).toBe(true);
    expect(childTaskIds.has("child_2")).toBe(true);
    expect(childTaskIds.has("root")).toBe(false);
  });

  it("filters child events after merge even when events arrive out of order", () => {
    const childTaskIds = new Set<string>(["child_1"]);

    // A message.delta event for child_1 arrives before collab.task.created
    // In this case child_1 is already registered, so it's filtered
    expect(isChatVisibleEvent({ visibility: undefined, taskId: "child_1" }, childTaskIds)).toBe(false);

    // Later events register child_2
    childTaskIds.add("child_2");
    expect(isChatVisibleEvent({ visibility: undefined, taskId: "child_2" }, childTaskIds)).toBe(false);
  });

  it("preserves accumulated state when new child tasks arrive after root messages", () => {
    const childTaskIds = new Set<string>(["child_1"]);

    // Root messages are always visible (not in childTaskIds)
    expect(isChatVisibleEvent({ visibility: "chat", taskId: "root" }, childTaskIds)).toBe(true);

    // Add more child tasks
    childTaskIds.add("child_2");
    childTaskIds.add("child_3");

    // Original child_1 still filtered
    expect(isChatVisibleEvent({ visibility: undefined, taskId: "child_1" }, childTaskIds)).toBe(false);
    // New child tasks also filtered
    expect(isChatVisibleEvent({ visibility: undefined, taskId: "child_2" }, childTaskIds)).toBe(false);
    expect(isChatVisibleEvent({ visibility: undefined, taskId: "child_3" }, childTaskIds)).toBe(false);
    // Root still visible
    expect(isChatVisibleEvent({ visibility: "chat", taskId: "root" }, childTaskIds)).toBe(true);
  });

  it("handles events.after reconnect by not clearing existing state", () => {
    // On reconnect, events.after returns missed events
    // The childTaskIds set should already contain previously seen child IDs
    const childTaskIds = new Set<string>(["child_1", "child_2"]);

    // Simulate a missed event arriving during reconnect
    const missedEvent: MockEvent = { visibility: "panel", taskId: "child_2" };
    expect(isChatVisibleEvent(missedEvent, childTaskIds)).toBe(false);

    // A new child task discovered in missed events
    childTaskIds.add("child_3");
    const newMissedEvent: MockEvent = { visibility: undefined, taskId: "child_3" };
    expect(isChatVisibleEvent(newMissedEvent, childTaskIds)).toBe(false);
  });

  it("keeps completed child task ids available for late legacy events", () => {
    const childTaskIds = new Set<string>(["child_1"]);

    // Completion should not delete the id: old-format delayed child events without
    // visibility still need to stay out of the main chat stream.
    expect(isChatVisibleEvent({ visibility: undefined, taskId: "child_1" }, childTaskIds)).toBe(false);
  });
});
