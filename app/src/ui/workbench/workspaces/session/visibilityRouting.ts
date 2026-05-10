export interface VisibilityRoutedEvent {
  taskId: string;
  visibility?: "chat" | "panel" | "trace";
}

export function isChatVisibleEvent(
  event: VisibilityRoutedEvent,
  childTaskIds: ReadonlySet<string>,
): boolean {
  if (event.visibility === "chat") return true;
  if (event.visibility === "panel" || event.visibility === "trace") return false;
  return !childTaskIds.has(event.taskId);
}
