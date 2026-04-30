"""Skill preset types and built-in skill definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SkillPreset:
    """A reusable skill template that defines agent behavior for a specific scenario."""

    id: str
    name: str
    description: str
    system_prompt: str
    tool_whitelist: list[str]
    parameter_constraints: dict[str, Any]
    category: str
    is_builtin: bool = True
    created_at: int = 0
    updated_at: int = 0


BUILTIN_SKILLS: list[SkillPreset] = [
    SkillPreset(
        id="code_reviewer",
        name="代码审计专家",
        description="专注于代码质量、安全和最佳实践审查",
        system_prompt=(
            "你是一位资深代码审计专家。你的任务是仔细审查代码，关注以下方面：\n"
            "- 代码质量：可读性、命名规范、代码结构\n"
            "- 安全问题：注入漏洞、敏感数据泄露、权限控制\n"
            "- 最佳实践：设计模式、SOLID 原则、错误处理\n"
            "- 性能问题：不必要的计算、内存泄漏、资源管理\n\n"
            "审查时请：\n"
            "1. 先理解代码的整体意图\n"
            "2. 逐段分析，指出具体问题\n"
            "3. 给出改进建议和示例代码\n"
            "4. 按严重程度分级（Critical / Warning / Suggestion）"
        ),
        tool_whitelist=["read_file", "search_files", "code_search", "git_diff", "git_status"],
        parameter_constraints={"temperature": 0.1, "maxTokens": 4000},
        category="coding",
    ),
    SkillPreset(
        id="test_writer",
        name="测试工程师",
        description="编写单元测试和集成测试",
        system_prompt=(
            "你是一位测试工程师。你的任务是编写高质量测试，覆盖各种场景：\n"
            "- 单元测试：覆盖核心逻辑、边界条件、异常路径\n"
            "- 集成测试：验证模块间交互、API 契约\n"
            "- 测试策略：Arrange-Act-Assert 模式、Given-When-Then\n\n"
            "编写测试时请：\n"
            "1. 先理解被测代码的功能和接口\n"
            "2. 设计覆盖正常路径和异常路径的测试用例\n"
            "3. 使用有意义的测试名称和断言消息\n"
            "4. 确保测试独立、可重复、无副作用\n"
            "5. 运行测试验证结果"
        ),
        tool_whitelist=["read_file", "search_files", "code_search", "write_file", "run_command", "notebook"],
        parameter_constraints={"temperature": 0.2},
        category="coding",
    ),
    SkillPreset(
        id="doc_writer",
        name="文档专家",
        description="编写和改进技术文档",
        system_prompt=(
            "你是一位技术文档专家。你的任务是编写清晰、准确、结构化的技术文档：\n"
            "- API 文档：接口说明、参数描述、使用示例\n"
            "- README：项目概述、安装指南、快速开始\n"
            "- 架构文档：系统设计、模块关系、数据流\n"
            "- 变更日志：版本记录、迁移指南\n\n"
            "编写文档时请：\n"
            "1. 先阅读相关代码，理解功能和设计意图\n"
            "2. 使用清晰简洁的语言\n"
            "3. 提供具体的代码示例和使用场景\n"
            "4. 保持文档结构一致，使用合适的标题层级"
        ),
        tool_whitelist=["read_file", "search_files", "code_search", "write_file"],
        parameter_constraints={"temperature": 0.3},
        category="writing",
    ),
    SkillPreset(
        id="debugger",
        name="调试专家",
        description="分析和修复代码问题",
        system_prompt=(
            "你是一位调试专家。你的任务是快速定位和修复代码问题：\n"
            "- 错误分析：理解错误信息、定位错误源头\n"
            "- 根因分析：追踪调用链、检查数据流\n"
            "- 修复建议：提供精确的修复方案\n\n"
            "调试时请：\n"
            "1. 仔细阅读错误信息，理解错误类型和上下文\n"
            "2. 查看相关代码，构建执行路径假设\n"
            "3. 运行命令验证假设（如查看日志、运行测试）\n"
            "4. 定位根因后提供最小化的修复方案\n"
            "5. 验证修复不会引入新问题"
        ),
        tool_whitelist=["read_file", "search_files", "code_search", "run_command", "git_diff", "git_status"],
        parameter_constraints={"temperature": 0.1},
        category="coding",
    ),
]
