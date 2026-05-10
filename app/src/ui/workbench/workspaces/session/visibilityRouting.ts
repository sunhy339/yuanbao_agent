import type { AgentEventEnvelope } from "@shared";

export function isChatVisibleEvent(
  event: Pick<AgentEventEnvelope, "taskId" | "visibility">,
  childTaskIds: ReadonlySet<string>,
): boolean {
  if (event.visibility === "chat") return true;
  if (event.visibility === "panel" || event.visibility === "trace") return false;
  return !childTaskIds.has(event.taskId);
}
