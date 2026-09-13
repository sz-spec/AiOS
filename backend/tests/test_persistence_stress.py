"""
Persistence Stress Test
=======================
Validates the dual-write pattern under concurrent load.

Tests:
1. 10 concurrent threads creating entities/records
2. Cache-repo consistency after concurrent writes
3. Service restart simulation (clear cache, verify repo has data)
4. Workflow + execution dual-write
5. MissionControl approval + alert dual-write
6. Error resilience (repo failures don't break in-memory cache)
7. Field name consistency audit (organization_id, not org_id, in repo calls)
"""

import os
import threading

# Force memory backend for testing
os.environ["VOS3_STORAGE_BACKEND"] = "memory"

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.repositories.memory import (
    InMemoryEntityRepository,
    InMemoryRecordRepository,
    InMemoryWorkflowRepository,
    InMemoryWorkflowExecutionRepository,
    InMemoryApprovalRepository,
    InMemoryAlertRepository,
)
from core.business_core import BusinessCoreService
from core.workflow_engine import WorkflowEngineService
from core.mission_control import MissionControlService, AlertSeverity

ORG_ID = "org_stress_test"
USER_ID = "user_stress_test"


class TestConcurrentDualWrite:
    """Test dual-write under concurrent load."""

    def setup_method(self):
        self.entity_repo = InMemoryEntityRepository()
        self.record_repo = InMemoryRecordRepository()
        self.service = BusinessCoreService(
            entity_repo=self.entity_repo,
            record_repo=self.record_repo,
            use_persistence=True,
        )

    def test_concurrent_entity_creation(self):
        """10 threads each create 5 entities → 50 total."""
        errors = []

        def create_entities(thread_id):
            try:
                for i in range(5):
                    name = f"entity_t{thread_id}_i{i}"
                    self.service.create_entity(
                        org_id=ORG_ID,
                        name=name,
                        label=f"Entity {thread_id}-{i}",
                        fields=[{"name": "title", "type": "text"}],
                    )
            except Exception as e:
                errors.append((thread_id, str(e)))

        threads = [
            threading.Thread(target=create_entities, args=(t,)) for t in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Thread errors: {errors}"

        # Verify in-memory cache
        cache_entities = [
            e for e in self.service._entities.values() if e.organization_id == ORG_ID
        ]
        assert (
            len(cache_entities) == 50
        ), f"Cache has {len(cache_entities)} entities, expected 50"

        # Verify repository
        repo_entities = self.entity_repo.list_by_org(ORG_ID)
        assert (
            len(repo_entities) == 50
        ), f"Repo has {len(repo_entities)} entities, expected 50"

    def test_concurrent_record_creation(self):
        """Create entity, then 10 threads each create 5 records."""
        entity = self.service.create_entity(
            org_id=ORG_ID,
            name="test_records",
            label="Test Records",
            fields=[{"name": "value", "type": "text"}],
        )
        errors = []

        def create_records(thread_id):
            try:
                for i in range(5):
                    self.service.create_record(
                        entity_id=entity.id,
                        org_id=ORG_ID,
                        data={"value": f"thread_{thread_id}_record_{i}"},
                        user_id=USER_ID,
                    )
            except Exception as e:
                errors.append((thread_id, str(e)))

        threads = [
            threading.Thread(target=create_records, args=(t,)) for t in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Thread errors: {errors}"

        # Cache consistency
        cache_records = [
            r for r in self.service._records.values() if r.entity_id == entity.id
        ]
        assert (
            len(cache_records) == 50
        ), f"Cache has {len(cache_records)} records, expected 50"

        # Repo consistency
        repo_records = self.record_repo.list_by_entity(entity.id)
        assert (
            len(repo_records) == 50
        ), f"Repo has {len(repo_records)} records, expected 50"

    def test_cache_repo_consistency(self):
        """After writes, cache count == repo count."""
        for i in range(20):
            self.service.create_entity(
                org_id=ORG_ID,
                name=f"consistency_{i}",
                label=f"Ent {i}",
                fields=[{"name": "x", "type": "text"}],
            )

        cache_count = len(
            [e for e in self.service._entities.values() if e.organization_id == ORG_ID]
        )
        repo_count = len(self.entity_repo.list_by_org(ORG_ID))
        assert cache_count == repo_count, f"Cache={cache_count} vs Repo={repo_count}"


class TestRestartSimulation:
    """Test that data survives in repo after cache is cleared."""

    def test_entity_survives_restart(self):
        """Clear in-memory cache, verify data still in repo."""
        entity_repo = InMemoryEntityRepository()
        record_repo = InMemoryRecordRepository()

        # Phase 1: Create data with service
        svc = BusinessCoreService(
            entity_repo=entity_repo,
            record_repo=record_repo,
            use_persistence=True,
        )
        entities_created = []
        for i in range(10):
            e = svc.create_entity(
                org_id=ORG_ID,
                name=f"survive_{i}",
                label=f"Survive {i}",
                fields=[{"name": "val", "type": "text"}],
            )
            entities_created.append(e.id)

        # Phase 2: Simulate restart — clear the in-memory cache
        svc._entities.clear()
        assert len(svc._entities) == 0, "Cache should be empty after clear"

        # Phase 3: Verify repo still has data
        repo_entities = entity_repo.list_by_org(ORG_ID)
        assert len(repo_entities) == 10, f"Repo lost data: {len(repo_entities)}"

        # Verify all IDs present
        repo_ids = {e["id"] for e in repo_entities}
        for eid in entities_created:
            assert eid in repo_ids, f"Entity {eid} missing from repo after restart"


class TestWorkflowDualWrite:
    """Test WorkflowEngine persistence."""

    def test_workflow_persists(self):
        """Create workflow → verify in repo."""
        wf_repo = InMemoryWorkflowRepository()
        exec_repo = InMemoryWorkflowExecutionRepository()
        svc = WorkflowEngineService(
            workflow_repo=wf_repo,
            execution_repo=exec_repo,
            use_persistence=True,
        )

        wf = svc.create_workflow(
            org_id=ORG_ID,
            name="Test Workflow",
            user_id=USER_ID,
            description="Stress test workflow",
        )

        # Verify in-memory
        assert wf.id in svc._workflows

        # Verify repo
        repo_workflows = wf_repo.list_by_org(ORG_ID)
        assert len(repo_workflows) >= 1, "Workflow not persisted to repo"
        assert any(w["name"] == "Test Workflow" for w in repo_workflows)


class TestMissionControlDualWrite:
    """Test MissionControl persistence."""

    def test_approval_persists(self):
        """Create approval → verify in repo."""
        approval_repo = InMemoryApprovalRepository()
        alert_repo = InMemoryAlertRepository()
        svc = MissionControlService(
            approval_repo=approval_repo,
            alert_repo=alert_repo,
            use_persistence=True,
        )

        approval = svc.create_approval(
            org_id=ORG_ID,
            title="Test Approval",
            category="data_change",
            requested_by_type="user",
            requested_by_id=USER_ID,
            requested_by_name="Stress Tester",
            action_type="create_entity",
            action_data={"entity": "customers"},
        )

        assert approval.id in svc._approvals
        repo_approvals = approval_repo.list_by_org(ORG_ID)
        assert len(repo_approvals) >= 1, "Approval not persisted to repo"

    def test_alert_persists(self):
        """Create alert → verify in repo."""
        approval_repo = InMemoryApprovalRepository()
        alert_repo = InMemoryAlertRepository()
        svc = MissionControlService(
            approval_repo=approval_repo,
            alert_repo=alert_repo,
            use_persistence=True,
        )

        alert = svc.create_alert(
            org_id=ORG_ID,
            title="Test Alert",
            message="Stress test alert message",
            severity=AlertSeverity.WARNING,
            category="system",
        )

        assert any(a.id == alert.id for a in svc._alerts)
        repo_alerts = alert_repo.list_by_org(ORG_ID)
        assert len(repo_alerts) >= 1, "Alert not persisted to repo"


class TestErrorResilience:
    """Test that repo failures don't break in-memory cache."""

    def test_repo_failure_graceful_degradation(self):
        """Mock repo to fail 100% of the time — cache still works."""
        entity_repo = InMemoryEntityRepository()
        record_repo = InMemoryRecordRepository()
        svc = BusinessCoreService(
            entity_repo=entity_repo,
            record_repo=record_repo,
            use_persistence=True,
        )

        # Patch repo.create to always fail
        original_create = entity_repo.create
        call_count = {"total": 0, "failed": 0}

        def failing_create(data):
            call_count["total"] += 1
            call_count["failed"] += 1
            raise RuntimeError("Simulated repo failure")

        entity_repo.create = failing_create

        # Create 10 entities — all should succeed in-memory despite repo failures
        for i in range(10):
            e = svc.create_entity(
                org_id=ORG_ID,
                name=f"resilient_{i}",
                label=f"Resilient {i}",
                fields=[{"name": "x", "type": "text"}],
            )
            assert e is not None, f"Entity {i} should still be created in memory"

        # Verify in-memory has all 10
        cache_entities = [
            e for e in svc._entities.values() if e.organization_id == ORG_ID
        ]
        assert (
            len(cache_entities) == 10
        ), f"Cache should have 10, got {len(cache_entities)}"
        assert call_count["failed"] == 10, "Repo should have been called 10 times"

        # Repo should be empty (all writes failed)
        entity_repo.create = original_create  # restore for list_by_org
        repo_entities = entity_repo.list_by_org(ORG_ID)
        assert len(repo_entities) == 0, "Repo should be empty after failures"


class TestFieldNameConsistency:
    """Audit: verify services use organization_id (not org_id) in repo calls."""

    def test_entity_repo_uses_organization_id(self):
        """Verify entity repo receives organization_id field."""
        entity_repo = InMemoryEntityRepository()
        record_repo = InMemoryRecordRepository()
        svc = BusinessCoreService(
            entity_repo=entity_repo,
            record_repo=record_repo,
            use_persistence=True,
        )

        svc.create_entity(
            org_id=ORG_ID,
            name="field_audit",
            label="Audit",
            fields=[{"name": "x", "type": "text"}],
        )

        repo_entities = entity_repo.list_by_org(ORG_ID)
        assert len(repo_entities) >= 1, "Entity should be in repo"
        entity = repo_entities[0]
        assert (
            "organization_id" in entity
        ), f"Missing 'organization_id' field, keys: {list(entity.keys())}"
        assert entity["organization_id"] == ORG_ID

    def test_workflow_repo_uses_organization_id(self):
        """Verify workflow repo receives organization_id field."""
        wf_repo = InMemoryWorkflowRepository()
        exec_repo = InMemoryWorkflowExecutionRepository()
        svc = WorkflowEngineService(
            workflow_repo=wf_repo,
            execution_repo=exec_repo,
            use_persistence=True,
        )

        svc.create_workflow(
            org_id=ORG_ID,
            name="field_audit_wf",
            user_id=USER_ID,
        )

        repo_wfs = wf_repo.list_by_org(ORG_ID)
        assert len(repo_wfs) >= 1, "Workflow should be in repo"
        wf = repo_wfs[0]
        assert (
            "organization_id" in wf
        ), f"Missing 'organization_id', keys: {list(wf.keys())}"

    def test_alert_repo_uses_organization_id(self):
        """Verify alert repo receives organization_id field."""
        approval_repo = InMemoryApprovalRepository()
        alert_repo = InMemoryAlertRepository()
        svc = MissionControlService(
            approval_repo=approval_repo,
            alert_repo=alert_repo,
            use_persistence=True,
        )

        svc.create_alert(
            org_id=ORG_ID,
            title="audit",
            message="test",
            severity=AlertSeverity.INFO,
            category="system",
        )

        repo_alerts = alert_repo.list_by_org(ORG_ID)
        assert len(repo_alerts) >= 1, "Alert should be in repo"
        alert = repo_alerts[0]
        assert (
            "organization_id" in alert
        ), f"Missing 'organization_id', keys: {list(alert.keys())}"
