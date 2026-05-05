# 性能优化分析计划

## 一、后端模块分析（Python Runtime）

### 1. Provider 适配层 (`provider/`)
- **热点**：`adapter.py` 流式输出首 token 延迟、`openai_compatible.py` 连接复用
- **优化点**：HTTP 连接池复用、流式 chunk 缓冲策略、`cache.py` 命中率
- **指标**：首 token 延迟 (TTFT)、端到端延迟

### 2. 编排层 (`orchestrator/` + `orchestration/`)
- **热点**：ReAct 循环迭代开销、supervisor 调度延迟
- **优化点**：`service.py` 循环轮询间隔、`supervisor.py` 任务分发粒度、`swarm.py` 广播开销
- **指标**：单轮推理延迟、多轮总耗时

### 3. 规划器 (`planner/`)
- **热点**：DAG 执行器并行度、`decomposer.py` 分解开销
- **优化点**：`dag_executor.py` 并行任务上限、`coverage.py` 覆盖率计算开销
- **指标**：规划耗时、DAG 执行总时间

### 4. 上下文管理 (`context/`)
- **热点**：`compactor.py` 压缩算法开销、`token_budget.py` 预算计算
- **优化点**：压缩频率、`scratchpad.py` 读写延迟
- **指标**：上下文构建耗时

### 5. 工具执行 (`tools/`)
- **热点**：`run_command.py` 子进程启动开销、`apply_patch.py` diff 计算
- **优化点**：子进程预热、`_shared.py` 共享逻辑去重
- **指标**：工具调用延迟

### 6. 记忆系统 (`memory/`)
- **热点**：SQLite 查询延迟（`store.py`）、`retriever.py` 向量检索
- **优化点**：索引优化、批量写入
- **指标**：记忆检索延迟

### 7. Worker 进程 (`services/`)
- **热点**：`worker_process_runtime.py` 进程启动、`worker_health.py` 健康检查
- **优化点**：进程池复用、`worker_budget.py` 资源调度
- **指标**：Worker 启动时间、任务完成时间

### 8. RPC 层 (`rpc/`)
- **热点**：`server.py` 序列化/反序列化
- **优化点**：消息批处理、协议开销
- **指标**：RPC 调用延迟

---

## 二、前端模块分析（React + Tauri）

### 1. Tauri 通信层 (`src-tauri/`)
- **热点**：IPC 序列化开销、`lib.rs` 命令处理
- **优化点**：批量消息合并、二进制传输

### 2. 运行时客户端 (`src/lib/runtimeClient.ts`)
- **热点**：轮询间隔、消息缓冲
- **优化点**：WebSocket 替代轮询、增量更新

### 3. Workbench UI (`src/ui/workbench/`)
- **热点**：`ComposerDock.tsx` 输入响应、`AppShell.tsx` 重渲染
- **优化点**：防抖节流、React.memo 优化

### 4. V2 组件 (`src/ui/v2/`)
- **热点**：`RuntimeComponents.tsx` 状态更新频率
- **优化点**：虚拟列表、懒加载

### 5. 状态管理 (`src/state/`)
- **热点**：`chatMessages.ts` 消息列表更新
- **优化点**：不可变数据结构、增量 diff

---

## 三、优先级排序

| 优先级 | 模块 | 理由 |
|--------|------|------|
| P0 | Provider 适配层 | 首 token 延迟直接影响体感 |
| P0 | 前端 Tauri 通信 | IPC 轮询是最大卡顿源 |
| P1 | 编排层 ReAct 循环 | 多轮迭代累积延迟 |
| P1 | Worker 进程复用 | 冷启动慢 |
| P2 | 上下文压缩 | 非关键路径可异步 |
| P2 | 记忆检索 | SQLite 可优化索引 |
| P3 | 其他模块 | 锦上添花 |

## 四、执行方式

1. 每个 P0/P1 模块单独读代码 + 写优化方案
2. 用实际测试验证优化效果
3. 逐步推进，不贪多