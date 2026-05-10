"""Tests for P0.1: Decision Inventory."""

from __future__ import annotations

from local_agent_runtime.policy.decision_inventory import (
    INVENTORY,
    count_decisions,
    get_decision,
    get_decisions_by_domain,
    list_domains,
)


class TestDecisionInventoryCompleteness:
    def test_inventory_not_empty(self):
        assert len(INVENTORY) > 0

    def test_all_have_ids(self):
        for d in INVENTORY:
            assert d.id, f"Decision missing id: {d}"
            assert d.id.startswith("D-"), f"Decision id should start with D-: {d.id}"

    def test_all_ids_unique(self):
        ids = [d.id for d in INVENTORY]
        assert len(ids) == len(set(ids)), f"Duplicate ids: {[x for x in ids if ids.count(x) > 1]}"

    def test_all_have_domain(self):
        for d in INVENTORY:
            assert d.domain, f"Decision {d.id} missing domain"

    def test_all_have_current_rule(self):
        for d in INVENTORY:
            assert d.current_rule, f"Decision {d.id} missing current_rule"

    def test_all_have_llm_proposal(self):
        for d in INVENTORY:
            assert d.llm_proposal, f"Decision {d.id} missing llm_proposal"

    def test_all_have_fallback(self):
        for d in INVENTORY:
            assert d.fallback, f"Decision {d.id} missing fallback"

    def test_count_matches(self):
        assert count_decisions() == len(INVENTORY)


class TestDecisionLookup:
    def test_get_existing_decision(self):
        d = get_decision("D-MODE-001")
        assert d is not None
        assert d.domain == "task_mode_routing"

    def test_get_nonexistent_decision(self):
        assert get_decision("D-NONEXIST") is None

    def test_get_decisions_by_domain(self):
        decisions = get_decisions_by_domain("task_mode_routing")
        assert len(decisions) >= 3
        assert all(d.domain == "task_mode_routing" for d in decisions)

    def test_get_decisions_empty_domain(self):
        assert get_decisions_by_domain("nonexistent_domain") == []


class TestDomains:
    def test_domains_not_empty(self):
        domains = list_domains()
        assert len(domains) > 0

    def test_domains_are_sorted(self):
        domains = list_domains()
        assert domains == sorted(domains)

    def test_expected_domains_present(self):
        domains = set(list_domains())
        expected = {
            "task_mode_routing",
            "subagent_planning",
            "model_provider",
            "skill_selection",
            "tool_selection",
            "mcp_selection",
            "context_assembly",
            "memory",
            "artifact_report",
            "risk_approval",
            "retry_recovery",
            "frontend",
            "synthesis",
        }
        for d in expected:
            assert d in domains, f"Missing domain: {d}"

    def test_all_domains_represented(self):
        """Every domain in list_domains should have at least one decision."""
        for domain in list_domains():
            decisions = get_decisions_by_domain(domain)
            assert len(decisions) > 0, f"Domain {domain} has no decisions"
