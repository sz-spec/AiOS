"""
App Registry Service
=====================
Manages app installations, enabling, disabling, and updates.
Follows the stateless app pattern (Shopify model): apps derive context
from API sessions, not internal state. All data through VOS3 APIs.
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AppInstallation:
    app_id: str
    organization_id: str
    installed_by: str
    version: str
    enabled: bool = True
    granted_scopes: List[str] = field(default_factory=list)
    config: Dict = field(default_factory=dict)
    installed_at: str = ""

    def __post_init__(self):
        if not self.installed_at:
            self.installed_at = datetime.now(timezone.utc).isoformat()


class AppRegistry:
    """Central registry for installed apps.

    Stateless pattern: apps don't store state internally.
    All state derived from Convex or API sessions.
    """

    def __init__(self):
        self._installations: Dict[str, Dict[str, AppInstallation]] = (
            {}
        )  # org_id -> {app_id -> installation}

    def install(
        self,
        app_id: str,
        organization_id: str,
        installed_by: str,
        version: str,
        scopes: List[str],
        config: Optional[Dict] = None,
    ) -> AppInstallation:
        """Install an app for an organization."""
        installation = AppInstallation(
            app_id=app_id,
            organization_id=organization_id,
            installed_by=installed_by,
            version=version,
            granted_scopes=scopes,
            config=config or {},
        )

        if organization_id not in self._installations:
            self._installations[organization_id] = {}
        self._installations[organization_id][app_id] = installation

        # Persist to Convex
        self._persist_installation(installation)

        return installation

    def uninstall(self, app_id: str, organization_id: str) -> bool:
        """Uninstall an app from an organization."""
        org_apps = self._installations.get(organization_id, {})
        if app_id in org_apps:
            del org_apps[app_id]
            return True
        return False

    def enable(self, app_id: str, organization_id: str) -> bool:
        """Enable a disabled app."""
        installation = self.get(app_id, organization_id)
        if installation:
            installation.enabled = True
            return True
        return False

    def disable(self, app_id: str, organization_id: str) -> bool:
        """Disable an app without uninstalling."""
        installation = self.get(app_id, organization_id)
        if installation:
            installation.enabled = False
            return True
        return False

    def update(
        self, app_id: str, organization_id: str, version: str
    ) -> Optional[AppInstallation]:
        """Update an app to a new version."""
        installation = self.get(app_id, organization_id)
        if installation:
            installation.version = version
            return installation
        return None

    def get(self, app_id: str, organization_id: str) -> Optional[AppInstallation]:
        """Get installation for an app in an organization."""
        return self._installations.get(organization_id, {}).get(app_id)

    def list_installed(self, organization_id: str) -> List[AppInstallation]:
        """List all installed apps for an organization."""
        return list(self._installations.get(organization_id, {}).values())

    def is_installed(self, app_id: str, organization_id: str) -> bool:
        """Check if an app is installed."""
        inst = self.get(app_id, organization_id)
        return inst is not None and inst.enabled

    def _persist_installation(self, installation: AppInstallation):
        """Persist installation to Convex via the repositories layer."""
        try:
            from core.repositories import get_app_installation_repository

            get_app_installation_repository().install(
                app_id=installation.app_id,
                organization_id=installation.organization_id,
                installed_by=installation.installed_by,
                version=installation.version,
                granted_scopes=installation.granted_scopes,
                config=installation.config,
            )
        except Exception:
            pass


_registry: Optional[AppRegistry] = None


def get_app_registry() -> AppRegistry:
    global _registry
    if _registry is None:
        _registry = AppRegistry()
    return _registry
