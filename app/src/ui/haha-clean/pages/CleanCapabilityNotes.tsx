import { CLEAN_TRANSCRIPT_CAPABILITIES } from "../conversation/transcriptModel";

export const YUANBAO_CAPABILITY_NOTES = [
  {
    status: "已接入",
    item: "聊天正文、用户消息、思考块、工具块、命令、审批、文件改动、worklog 折叠、置底按钮、Composer 基础控件。",
  },
  {
    status: "已接入",
    item: "项目目录、上下文比例、权限模式、模型选择、附件入口、斜杠命令入口、待发送队列。",
  },
  {
    status: "需要后端补字段",
    item: "Yuanbao 风格的真实 token 级 thinking 流、工具 parent/child 调用树、每个工具的结构化输入/输出摘要、分支/撤销消息目标。",
  },
  {
    status: "需要后端补字段",
    item: "项目 git 分支/worktree 详细状态、上下文快照分类明细、文件引用/图片引用的持久化关联。",
  },
  {
    status: "暂时占位",
    item: "代码阅览的完整语法高亮、Markdown 文件专用预览、文件搜索/引用面板、Computer Use 权限弹窗。",
  },
];

export function CleanCapabilityNotes() {
  return (
    <section className="hc-capability-notes" aria-label="Yuanbao 能力接入记录">
      <h2>Yuanbao 能力接入记录</h2>
      <ul>
        {YUANBAO_CAPABILITY_NOTES.map((note) => (
          <li key={`${note.status}:${note.item}`}>
            <strong>{note.status}</strong>
            <span>{note.item}</span>
          </li>
        ))}
      </ul>
      <h3>信息流能力</h3>
      <ul>
        {CLEAN_TRANSCRIPT_CAPABILITIES.map((capability) => (
          <li key={capability.kind}>
            <strong>{capability.label}</strong>
            <span>{capability.current} · {capability.source}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
