"""
Tests for Workflow Engine
==========================

Covers: Condition operators, ConditionGroup, WorkflowEngineService CRUD,
        Node management, Trigger management, Template workflows
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.workflow_engine import (
    Condition,
    ConditionOperator,
    ConditionGroup,
    WorkflowEngineService,
    WorkflowStatus,
    ActionType,
    TriggerType,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """Fresh WorkflowEngineService (has sample data from _init_sample_data)."""
    return WorkflowEngineService()


@pytest.fixture
def empty_engine():
    """Engine with no sample data."""
    e = WorkflowEngineService()
    # Clear sample workflows
    e._workflows.clear()
    return e


# ---------------------------------------------------------------------------
# Condition.evaluate — all 14 operators
# ---------------------------------------------------------------------------


class TestConditionEvaluate:
    def test_eq(self):
        c = Condition(field="status", operator=ConditionOperator.EQUALS, value="active")
        assert c.evaluate({"status": "active"}) is True
        assert c.evaluate({"status": "inactive"}) is False

    def test_ne(self):
        c = Condition(
            field="status", operator=ConditionOperator.NOT_EQUALS, value="deleted"
        )
        assert c.evaluate({"status": "active"}) is True
        assert c.evaluate({"status": "deleted"}) is False

    def test_gt(self):
        c = Condition(field="value", operator=ConditionOperator.GREATER_THAN, value=100)
        assert c.evaluate({"value": 200}) is True
        assert c.evaluate({"value": 50}) is False
        assert c.evaluate({"value": 100}) is False

    def test_lt(self):
        c = Condition(field="value", operator=ConditionOperator.LESS_THAN, value=100)
        assert c.evaluate({"value": 50}) is True
        assert c.evaluate({"value": 200}) is False

    def test_gte(self):
        c = Condition(
            field="value", operator=ConditionOperator.GREATER_OR_EQUAL, value=100
        )
        assert c.evaluate({"value": 100}) is True
        assert c.evaluate({"value": 99}) is False

    def test_lte(self):
        c = Condition(
            field="value", operator=ConditionOperator.LESS_OR_EQUAL, value=100
        )
        assert c.evaluate({"value": 100}) is True
        assert c.evaluate({"value": 101}) is False

    def test_contains(self):
        c = Condition(field="tags", operator=ConditionOperator.CONTAINS, value="vip")
        assert c.evaluate({"tags": "vip-customer"}) is True
        assert c.evaluate({"tags": "normal"}) is False

    def test_not_contains(self):
        c = Condition(
            field="tags", operator=ConditionOperator.NOT_CONTAINS, value="spam"
        )
        assert c.evaluate({"tags": "good"}) is True
        assert c.evaluate({"tags": "spam-email"}) is False

    def test_starts_with(self):
        c = Condition(field="name", operator=ConditionOperator.STARTS_WITH, value="Dr.")
        assert c.evaluate({"name": "Dr. Smith"}) is True
        assert c.evaluate({"name": "Mr. Jones"}) is False

    def test_ends_with(self):
        c = Condition(
            field="email", operator=ConditionOperator.ENDS_WITH, value="@corp.com"
        )
        assert c.evaluate({"email": "user@corp.com"}) is True
        assert c.evaluate({"email": "user@gmail.com"}) is False

    def test_is_empty(self):
        c = Condition(field="notes", operator=ConditionOperator.IS_EMPTY, value=None)
        assert c.evaluate({"notes": ""}) is True
        assert c.evaluate({"notes": "hello"}) is False
        assert c.evaluate({}) is True

    def test_is_not_empty(self):
        c = Condition(
            field="notes", operator=ConditionOperator.IS_NOT_EMPTY, value=None
        )
        assert c.evaluate({"notes": "hello"}) is True
        assert c.evaluate({"notes": ""}) is False

    def test_in(self):
        c = Condition(
            field="status",
            operator=ConditionOperator.IN_LIST,
            value=["active", "trialing"],
        )
        assert c.evaluate({"status": "active"}) is True
        assert c.evaluate({"status": "canceled"}) is False

    def test_not_in(self):
        c = Condition(
            field="status",
            operator=ConditionOperator.NOT_IN_LIST,
            value=["deleted", "banned"],
        )
        assert c.evaluate({"status": "active"}) is True
        assert c.evaluate({"status": "deleted"}) is False

    def test_nested_field(self):
        c = Condition(
            field="trigger.data.value",
            operator=ConditionOperator.GREATER_THAN,
            value=100,
        )
        assert c.evaluate({"trigger": {"data": {"value": 200}}}) is True
        assert c.evaluate({"trigger": {"data": {"value": 50}}}) is False

    def test_missing_field(self):
        c = Condition(field="nonexistent", operator=ConditionOperator.EQUALS, value="x")
        assert c.evaluate({}) is False


# ---------------------------------------------------------------------------
# ConditionGroup.evaluate
# ---------------------------------------------------------------------------


class TestConditionGroup:
    def test_and_all_true(self):
        group = ConditionGroup(
            logic="and",
            conditions=[
                Condition(field="a", operator=ConditionOperator.EQUALS, value=1),
                Condition(field="b", operator=ConditionOperator.EQUALS, value=2),
            ],
        )
        assert group.evaluate({"a": 1, "b": 2}) is True

    def test_and_one_false(self):
        group = ConditionGroup(
            logic="and",
            conditions=[
                Condition(field="a", operator=ConditionOperator.EQUALS, value=1),
                Condition(field="b", operator=ConditionOperator.EQUALS, value=2),
            ],
        )
        assert group.evaluate({"a": 1, "b": 99}) is False

    def test_or_one_true(self):
        group = ConditionGroup(
            logic="or",
            conditions=[
                Condition(field="a", operator=ConditionOperator.EQUALS, value=1),
                Condition(field="b", operator=ConditionOperator.EQUALS, value=2),
            ],
        )
        assert group.evaluate({"a": 1, "b": 99}) is True

    def test_or_none_true(self):
        group = ConditionGroup(
            logic="or",
            conditions=[
                Condition(field="a", operator=ConditionOperator.EQUALS, value=1),
                Condition(field="b", operator=ConditionOperator.EQUALS, value=2),
            ],
        )
        assert group.evaluate({"a": 99, "b": 99}) is False

    def test_empty_group(self):
        group = ConditionGroup(logic="and", conditions=[])
        assert group.evaluate({}) is True


# ---------------------------------------------------------------------------
# WorkflowEngineService — CRUD
# ---------------------------------------------------------------------------


class TestWorkflowCRUD:
    def test_create_workflow(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "Test WF", "user1")
        assert wf.name == "Test WF"
        assert wf.organization_id == "org1"
        assert wf.created_by == "user1"
        assert wf.status == WorkflowStatus.DRAFT

    def test_get_workflow(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "Test", "user1")
        fetched = empty_engine.get_workflow(wf.id)
        assert fetched is not None
        assert fetched.id == wf.id

    def test_get_workflow_not_found(self, empty_engine):
        assert empty_engine.get_workflow("nonexistent") is None

    def test_list_workflows(self, empty_engine):
        empty_engine.create_workflow("org1", "WF1", "user1")
        empty_engine.create_workflow("org1", "WF2", "user1")
        empty_engine.create_workflow("org2", "WF3", "user2")
        result = empty_engine.list_workflows("org1")
        assert len(result) == 2

    def test_list_workflows_filter_status(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF1", "user1")
        empty_engine.activate_workflow(wf.id)
        empty_engine.create_workflow("org1", "WF2", "user1")
        result = empty_engine.list_workflows("org1", status=WorkflowStatus.ACTIVE)
        assert len(result) == 1

    def test_update_workflow(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "Old Name", "user1")
        updated = empty_engine.update_workflow(wf.id, {"name": "New Name"})
        assert updated.name == "New Name"

    def test_update_workflow_not_found(self, empty_engine):
        assert empty_engine.update_workflow("nope", {"name": "x"}) is None

    def test_delete_workflow(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "ToDelete", "user1")
        assert empty_engine.delete_workflow(wf.id) is True
        assert empty_engine.get_workflow(wf.id) is None

    def test_delete_workflow_not_found(self, empty_engine):
        assert empty_engine.delete_workflow("nope") is False

    def test_activate_workflow(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        activated = empty_engine.activate_workflow(wf.id)
        assert activated.status == WorkflowStatus.ACTIVE

    def test_pause_workflow(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        empty_engine.activate_workflow(wf.id)
        paused = empty_engine.pause_workflow(wf.id)
        assert paused.status == WorkflowStatus.PAUSED


# ---------------------------------------------------------------------------
# Node Management
# ---------------------------------------------------------------------------


class TestNodeManagement:
    def test_add_first_node_sets_start(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        node = empty_engine.add_node(wf.id, ActionType.SEND_EMAIL, "Email Node")
        assert node is not None
        assert wf.start_node_id == node.id

    def test_add_second_node_keeps_start(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        first = empty_engine.add_node(wf.id, ActionType.SEND_EMAIL, "First")
        empty_engine.add_node(wf.id, ActionType.DELAY, "Second")
        assert wf.start_node_id == first.id

    def test_add_node_invalid_workflow(self, empty_engine):
        assert empty_engine.add_node("nope", ActionType.DELAY, "X") is None

    def test_update_node(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        node = empty_engine.add_node(wf.id, ActionType.SEND_EMAIL, "Email")
        updated = empty_engine.update_node(wf.id, node.id, {"name": "Updated Email"})
        assert updated.name == "Updated Email"

    def test_delete_node(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        node = empty_engine.add_node(wf.id, ActionType.SEND_EMAIL, "Email")
        assert empty_engine.delete_node(wf.id, node.id) is True
        assert len(wf.nodes) == 0

    def test_delete_start_node_updates_start(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        first = empty_engine.add_node(wf.id, ActionType.SEND_EMAIL, "First")
        second = empty_engine.add_node(wf.id, ActionType.DELAY, "Second")
        empty_engine.delete_node(wf.id, first.id)
        assert wf.start_node_id == second.id

    def test_connect_nodes(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        n1 = empty_engine.add_node(wf.id, ActionType.SEND_EMAIL, "N1")
        n2 = empty_engine.add_node(wf.id, ActionType.DELAY, "N2")
        assert empty_engine.connect_nodes(wf.id, n1.id, n2.id) is True
        assert n2.id in n1.next_nodes

    def test_connect_nodes_success_type(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        n1 = empty_engine.add_node(wf.id, ActionType.CONDITION, "Check")
        n2 = empty_engine.add_node(wf.id, ActionType.SEND_EMAIL, "Email")
        empty_engine.connect_nodes(wf.id, n1.id, n2.id, "success")
        assert n1.on_success == n2.id


# ---------------------------------------------------------------------------
# Trigger Management
# ---------------------------------------------------------------------------


class TestTriggerManagement:
    def test_set_trigger(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        trigger = empty_engine.set_trigger(
            wf.id, TriggerType.EVENT, entity_id="contacts", event_type="created"
        )
        assert trigger is not None
        assert trigger.type == TriggerType.EVENT
        assert trigger.entity_id == "contacts"

    def test_set_trigger_not_found(self, empty_engine):
        assert empty_engine.set_trigger("nope", TriggerType.MANUAL) is None


# ---------------------------------------------------------------------------
# Workflow Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_workflow_to_dict(self, empty_engine):
        wf = empty_engine.create_workflow(
            "org1", "WF", "user1", description="A workflow"
        )
        d = wf.to_dict()
        assert d["name"] == "WF"
        assert d["organizationId"] == "org1"
        assert d["status"] == "draft"

    def test_node_to_dict(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        node = empty_engine.add_node(wf.id, ActionType.SEND_EMAIL, "Email")
        d = node.to_dict()
        assert d["name"] == "Email"
        assert d["type"] == "send_email"

    def test_condition_to_dict(self):
        c = Condition(field="x", operator=ConditionOperator.EQUALS, value=1)
        d = c.to_dict()
        assert d["field"] == "x"
        assert d["operator"] == "eq"
        assert d["value"] == 1


# ---------------------------------------------------------------------------
# Template Workflows
# ---------------------------------------------------------------------------


class TestTemplates:
    def test_sample_data_loaded(self, engine):
        wfs = engine.list_workflows("org_demo")
        assert len(wfs) == 3

    def test_welcome_email_template(self, engine):
        wfs = engine.list_workflows("org_demo")
        names = [w.name for w in wfs]
        assert "Welcome Email" in names

    def test_workflow_stats_empty(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        stats = empty_engine.get_workflow_stats(wf.id)
        assert stats["runCount"] == 0
        assert stats["successRate"] == 0

    def test_workflow_stats_not_found(self, empty_engine):
        assert empty_engine.get_workflow_stats("nope") == {}


# ---------------------------------------------------------------------------
# Execution History
# ---------------------------------------------------------------------------


class TestExecutionHistory:
    def test_get_execution_not_found(self, empty_engine):
        assert empty_engine.get_execution("nope") is None

    def test_list_executions_empty(self, empty_engine):
        wf = empty_engine.create_workflow("org1", "WF", "user1")
        assert empty_engine.list_executions(wf.id) == []
