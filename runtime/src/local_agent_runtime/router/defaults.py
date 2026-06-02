from __future__ import annotations

from typing import Any

from .types import ExecutionStrategy, Scenario

# ---------------------------------------------------------------------------
# Default scenario -> strategy mapping
# ---------------------------------------------------------------------------

SCENARIO_STRATEGY_MAP: dict[Scenario, dict[str, Any]] = {
    Scenario.SIMPLE_QUERY: {
        "strategy": ExecutionStrategy.REACT_FAST,
        "max_steps": 12,
        "enable_reflection": False,
        "enable_planning": False,
    },
    Scenario.CODE_SEARCH: {
        "strategy": ExecutionStrategy.REACT_FAST,
        "max_steps": 20,
        "enable_reflection": False,
        "enable_planning": False,
    },
    Scenario.CODE_EDIT: {
        "strategy": ExecutionStrategy.REACT_STANDARD,
        "max_steps": 35,
        "enable_reflection": True,
        "enable_planning": False,
    },
    Scenario.CODE_REVIEW: {
        "strategy": ExecutionStrategy.SKILL_BASED,
        "skill_id": "code_reviewer",
        "max_steps": 35,
        "enable_reflection": True,
        "enable_planning": False,
    },
    Scenario.DEBUG: {
        "strategy": ExecutionStrategy.SKILL_BASED,
        "skill_id": "debugger",
        "max_steps": 45,
        "enable_reflection": False,
        "enable_planning": False,
    },
    Scenario.TEST_WRITE: {
        "strategy": ExecutionStrategy.SKILL_BASED,
        "skill_id": "test_writer",
        "max_steps": 35,
        "enable_reflection": True,
        "enable_planning": False,
    },
    Scenario.DOC_WRITE: {
        "strategy": ExecutionStrategy.SKILL_BASED,
        "skill_id": "doc_writer",
        "max_steps": 35,
        "enable_reflection": False,
        "enable_planning": False,
    },
    Scenario.MULTI_STEP_TASK: {
        "strategy": ExecutionStrategy.PLAN_THEN_EXECUTE,
        "max_steps": 60,
        "enable_reflection": True,
        "enable_planning": True,
    },
    Scenario.SUPERVISED_TASK: {
        "strategy": ExecutionStrategy.PLAN_SUPERVISE,
        "max_steps": 80,
        "enable_reflection": True,
        "enable_planning": True,
    },
    Scenario.SWARM_TASK: {
        "strategy": ExecutionStrategy.PLAN_SWARM,
        "max_steps": 100,
        "enable_reflection": True,
        "enable_planning": True,
    },
    Scenario.FREE_FORM: {
        "strategy": ExecutionStrategy.REACT_STANDARD,
        "max_steps": 30,
        "enable_reflection": False,
        "enable_planning": False,
    },
}

# ---------------------------------------------------------------------------
# Keyword-based routing rules  (scenario, keywords, base confidence)
# ---------------------------------------------------------------------------

_ROUTE_RULES: list[tuple[Scenario, list[str], float]] = [
    (Scenario.CODE_REVIEW, ["审查", "review", "audit", "检查代码", "安全检查", "代码质量", "代码审查"], 0.85),
    (Scenario.DEBUG, ["调试", "debug", "报错", "bug", "错误", "崩溃", "异常", "error", "crash", "traceback", "为什么不", "为什么报"], 0.85),
    (Scenario.TEST_WRITE, ["测试", "test", "spec", "单元测试", "集成测试", "test case", "test case"], 0.85),
    (Scenario.DOC_WRITE, ["文档", "doc", "readme", "注释", "说明", "文档化"], 0.85),
    (Scenario.MULTI_STEP_TASK, ["重构", "refactor", "迁移", "migrate", "整体", "所有模块", "多个文件", "全部重写"], 0.80),
    (Scenario.SUPERVISED_TASK, ["监督", "审核执行", "supervised", "review plan", "监督执行", "审核计划"], 0.80),
    (Scenario.SWARM_TASK, ["协作", "多agent", "swarm", "collaborate", "协同", "多个智能体", "群体协作"], 0.80),
    (Scenario.CODE_SEARCH, ["搜索", "查找", "找", "在哪", "search", "find", "where", "locate"], 0.80),
    (Scenario.CODE_EDIT, ["修改", "改", "更新", "添加", "删除", "新增", "实现", "写一个", "创建", "新增"], 0.70),
    (Scenario.SIMPLE_QUERY, ["是什么", "怎么", "如何", "解释", "what is", "how to", "explain", "是什么意思", "介绍一下"], 0.75),
]

_ROUTE_RULES.append((
    Scenario.CODE_EDIT,
    ["edit", "change", "modify", "update", "implement", "add", "fix", "write"],
    0.70,
))

# Build a reverse lookup: lowercase keyword -> (scenario, confidence)
_KEYWORD_INDEX: dict[str, tuple[Scenario, float]] = {}
for _scenario, _keywords, _conf in _ROUTE_RULES:
    for _kw in _keywords:
        _kw_lower = _kw.lower()
        # Keep the higher confidence when two rules share a keyword
        if _kw_lower not in _KEYWORD_INDEX or _conf > _KEYWORD_INDEX[_kw_lower][1]:
            _KEYWORD_INDEX[_kw_lower] = (_scenario, _conf)


def lookup_keyword(keyword: str) -> tuple[Scenario, float] | None:
    """Return (scenario, confidence) for *keyword*, or ``None``."""
    return _KEYWORD_INDEX.get(keyword.lower())
