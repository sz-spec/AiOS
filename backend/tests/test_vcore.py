"""
V-Core Integration Tests
========================
Tests for all V-Core services (Control Plane, Business Core, Workflow Engine, Mission Control).

Run with: pytest tests/test_vcore.py -v
"""

import pytest
from core import (
    get_control_plane_service,
    get_business_core_service,
    get_workflow_engine_service,
    get_mission_control_service,
)
from core.mission_control import AlertSeverity


class TestControlPlane:
    """Tests for Control Plane service (users, orgs, RBAC)."""

    def setup_method(self):
        """Get fresh service instance for each test."""
        self.cp = get_control_plane_service()

    def test_create_user(self):
        """Test user creation."""
        user = self.cp.create_user(
            email="test@example.com", name="Test User", password="securepassword123"
        )
        assert user is not None
        assert user.email == "test@example.com"
        assert user.name == "Test User"
        assert user.id.startswith("user_")

    def test_create_organization(self):
        """Test organization creation."""
        # First create owner
        user = self.cp.create_user(
            email="owner@example.com", name="Org Owner", password="test123"
        )

        org = self.cp.create_organization(name="Test Organization", owner_id=user.id)
        assert org is not None
        assert org.name == "Test Organization"
        assert org.owner_id == user.id
        assert org.id.startswith("org_")

    def test_get_user_by_email(self):
        """Test user retrieval by email."""
        email = "lookup@example.com"
        self.cp.create_user(email=email, name="Lookup User", password="test123")

        user = self.cp.get_user_by_email(email)
        assert user is not None
        assert user.email == email


class TestBusinessCore:
    """Tests for Business Core service (entities, records)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.cp = get_control_plane_service()
        self.bc = get_business_core_service()

        # Create test user and org
        self.user = self.cp.create_user(
            email=f"bc_test_{id(self)}@example.com",
            name="BC Test User",
            password="test123",
        )
        self.org = self.cp.create_organization(
            name=f"BC Test Org {id(self)}", owner_id=self.user.id
        )

    def test_create_entity(self):
        """Test entity creation."""
        entity = self.bc.create_entity(
            org_id=self.org.id,
            name="contacts",
            label="Contact",
            fields=[
                {"name": "name", "label": "Name", "type": "text", "required": True},
                {"name": "email", "label": "Email", "type": "email", "required": True},
            ],
        )
        assert entity is not None
        assert entity.name == "contacts"
        assert entity.label == "Contact"
        assert entity.id.startswith("ent_")

    def test_create_record(self):
        """Test record creation."""
        entity = self.bc.create_entity(org_id=self.org.id, name="tasks", label="Task")

        record = self.bc.create_record(
            entity_id=entity.id,
            org_id=self.org.id,
            data={"title": "Test Task", "status": "pending"},
            user_id=self.user.id,
        )
        assert record is not None
        assert record.id.startswith("rec_")
        # Data is stored in record.data dict
        assert record.data is not None

    def test_list_records_by_entity(self):
        """Test record retrieval by entity."""
        entity = self.bc.create_entity(org_id=self.org.id, name="notes", label="Note")

        # Create multiple records
        for i in range(3):
            self.bc.create_record(
                entity_id=entity.id,
                org_id=self.org.id,
                data={"content": f"Note {i}"},
                user_id=self.user.id,
            )

        records = self.bc.list_records(entity_id=entity.id, org_id=self.org.id)
        assert len(records) >= 3


class TestWorkflowEngine:
    """Tests for Workflow Engine service."""

    def setup_method(self):
        """Set up test fixtures."""
        self.cp = get_control_plane_service()
        self.wf = get_workflow_engine_service()

        self.user = self.cp.create_user(
            email=f"wf_test_{id(self)}@example.com",
            name="WF Test User",
            password="test123",
        )
        self.org = self.cp.create_organization(
            name=f"WF Test Org {id(self)}", owner_id=self.user.id
        )

    def test_create_workflow(self):
        """Test workflow creation."""
        workflow = self.wf.create_workflow(
            org_id=self.org.id,
            name="Email Notification",
            user_id=self.user.id,
            description="Send email on new record",
        )
        assert workflow is not None
        assert workflow.name == "Email Notification"
        assert workflow.id.startswith("wf_")

    def test_get_workflow(self):
        """Test workflow retrieval."""
        created = self.wf.create_workflow(
            org_id=self.org.id, name="Retrieval Test", user_id=self.user.id
        )

        retrieved = self.wf.get_workflow(created.id)
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.name == created.name


class TestMissionControl:
    """Tests for Mission Control service (monitoring, alerts)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.cp = get_control_plane_service()
        self.mc = get_mission_control_service()

        self.user = self.cp.create_user(
            email=f"mc_test_{id(self)}@example.com",
            name="MC Test User",
            password="test123",
        )
        self.org = self.cp.create_organization(
            name=f"MC Test Org {id(self)}", owner_id=self.user.id
        )

    def test_create_alert(self):
        """Test alert creation."""
        alert = self.mc.create_alert(
            org_id=self.org.id,
            title="High CPU Usage",
            message="CPU usage exceeded 90%",
            severity=AlertSeverity.WARNING,
            category="system",
        )
        assert alert is not None
        assert alert.title == "High CPU Usage"
        assert alert.severity == AlertSeverity.WARNING
        assert alert.id.startswith("alert_")

    def test_alert_severity_levels(self):
        """Test different alert severity levels."""
        severities = [
            AlertSeverity.INFO,
            AlertSeverity.WARNING,
            AlertSeverity.ERROR,
            AlertSeverity.CRITICAL,
        ]

        for severity in severities:
            alert = self.mc.create_alert(
                org_id=self.org.id,
                title=f"Test {severity.value}",
                message=f"Testing {severity.value} severity",
                severity=severity,
                category="test",
            )
            assert alert.severity == severity


class TestVCoreIntegration:
    """Integration tests across V-Core services."""

    def test_full_workflow(self):
        """Test complete V-Core workflow: user → org → entity → record → alert."""
        # Get all services
        cp = get_control_plane_service()
        bc = get_business_core_service()
        wf = get_workflow_engine_service()
        mc = get_mission_control_service()

        # 1. Create user
        user = cp.create_user(
            email="integration@example.com", name="Integration Test", password="test123"
        )
        assert user.id.startswith("user_")

        # 2. Create organization
        org = cp.create_organization(name="Integration Org", owner_id=user.id)
        assert org.id.startswith("org_")

        # 3. Create entity
        entity = bc.create_entity(org_id=org.id, name="leads", label="Lead")
        assert entity.id.startswith("ent_")

        # 4. Create record
        record = bc.create_record(
            entity_id=entity.id,
            org_id=org.id,
            data={"company": "Acme Inc", "value": 10000},
            user_id=user.id,
        )
        assert record.id.startswith("rec_")

        # 5. Create workflow
        workflow = wf.create_workflow(
            org_id=org.id, name="Lead Notification", user_id=user.id
        )
        assert workflow.id.startswith("wf_")

        # 6. Create alert
        alert = mc.create_alert(
            org_id=org.id,
            title="New High-Value Lead",
            message=f"Lead {record.id} worth $10,000",
            severity=AlertSeverity.INFO,
            category="sales",
        )
        assert alert.id.startswith("alert_")

        print("\n✅ Full V-Core integration test passed!")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
