from local_agent_runtime.orchestrator.message_execution import MessageExecutionMixin


def test_read_only_verification_task_is_not_plan_only():
    goal = """
    Please inspect README.md and src/ledger.py, then run:

    python -m pytest tests -q

    Do not modify files. Summarize the command result.
    """

    assert MessageExecutionMixin._is_plan_only_goal(goal) is False


def test_explicit_plan_only_task_stays_plan_only():
    goal = "Plan only: do not execute commands. Propose a migration strategy."

    assert MessageExecutionMixin._is_plan_only_goal(goal) is True


def test_no_implementation_without_execution_request_stays_plan_only():
    goal = "Design the architecture, do not implement or create files."

    assert MessageExecutionMixin._is_plan_only_goal(goal) is True
