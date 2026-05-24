from local_agent_runtime.orchestrator.task_lifecycle import _merge_active_assistant_completion_content


def test_completion_merge_keeps_existing_content_when_final_summary_repeats_it() -> None:
    previous = "Ran pytest and py_compile. Both checks passed."
    final = "Ran pytest and py_compile. Both checks passed."

    assert _merge_active_assistant_completion_content(previous, final) == previous


def test_completion_merge_keeps_richer_stream_when_final_summary_is_near_duplicate() -> None:
    previous = "I inspected the workspace, ran pytest, then ran py_compile. Both checks passed."
    final = "I inspected the workspace and ran pytest, then py_compile. Both checks passed."

    assert _merge_active_assistant_completion_content(previous, final) == previous


def test_completion_merge_appends_distinct_final_summary() -> None:
    previous = "I inspected the workspace."
    final = "Both verification commands passed."

    assert _merge_active_assistant_completion_content(previous, final) == (
        "I inspected the workspace.\n\nBoth verification commands passed."
    )
