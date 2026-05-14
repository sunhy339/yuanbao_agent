export function TraceFilterBar({
  filter,
  onChange,
  taskIds,
  agentTypes,
}: {
  filter: { taskId: string; visibility: "" | "chat" | "panel" | "trace"; agentType: string };
  onChange: (next: { taskId: string; visibility: "" | "chat" | "panel" | "trace"; agentType: string }) => void;
  taskIds: string[];
  agentTypes: string[];
}) {
  const hasAnyFilter = filter.taskId || filter.visibility || filter.agentType;
  return (
    <div className="trace-filter-bar" aria-label="诊断过滤">
      <label>
        <span>任务</span>
        <select
          value={filter.taskId}
          onChange={(e) => onChange({ ...filter, taskId: e.target.value })}
        >
          <option value="">全部</option>
          {taskIds.map((id) => (
            <option key={id} value={id}>{id}</option>
          ))}
        </select>
      </label>
      <label>
        <span>可见性</span>
        <select
          value={filter.visibility}
          onChange={(e) => onChange({ ...filter, visibility: e.target.value as "" | "chat" | "panel" | "trace" })}
        >
          <option value="">全部</option>
          <option value="chat">chat</option>
          <option value="panel">panel</option>
          <option value="trace">trace</option>
        </select>
      </label>
      <label>
        <span>Agent</span>
        <select
          value={filter.agentType}
          onChange={(e) => onChange({ ...filter, agentType: e.target.value })}
        >
          <option value="">全部</option>
          {agentTypes.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </label>
      {hasAnyFilter ? (
        <button
          type="button"
          className="trace-filter-clear"
          onClick={() => onChange({ taskId: "", visibility: "", agentType: "" })}
        >
          清除
        </button>
      ) : null}
    </div>
  );
}
