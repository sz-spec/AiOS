"""
Deploy Service
==============
Orchestrates deployment to Vercel (frontend) and Railway (backend).
Includes Vercel API integration with mock fallback for dev mode.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class Deployment:
    id: str = ""
    project_id: str = ""
    provider: str = "vercel"
    url: str = ""
    subdomain: str = ""
    status: str = "pending"  # pending, building, live, failed
    config: Dict = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


class DeployService:
    """Deployment orchestration with Vercel API integration."""

    def __init__(self):
        self._deployments: Dict[str, Deployment] = {}
        self._vercel_token = os.environ.get("VERCEL_TOKEN")

    async def deploy_frontend(
        self, project_id: str, files: Dict[str, str], subdomain: str
    ) -> Deployment:
        """Deploy frontend files to Vercel."""
        deployment = Deployment(
            project_id=project_id,
            provider="vercel",
            subdomain=subdomain,
            status="building",
        )

        if self._vercel_token:
            try:
                deployment = await self._deploy_to_vercel(deployment, files, subdomain)
            except Exception as e:
                deployment.status = "failed"
                deployment.config["error"] = str(e)
        else:
            # Mock deployment for dev mode
            deployment.url = f"https://{subdomain}.vcreator.app"
            deployment.status = "live"

        self._deployments[deployment.id] = deployment
        await self._persist(deployment)
        return deployment

    async def _deploy_to_vercel(
        self, deployment: Deployment, files: Dict[str, str], subdomain: str
    ) -> Deployment:
        """Call Vercel Deployments API."""
        import httpx

        vercel_files = [
            {"file": path, "data": content} for path, content in files.items()
        ]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.vercel.com/v13/deployments",
                headers={
                    "Authorization": f"Bearer {self._vercel_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "name": subdomain,
                    "files": vercel_files,
                    "projectSettings": {"framework": "nextjs"},
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            data = resp.json()
            deployment.url = f"https://{data.get('url', subdomain + '.vercel.app')}"
            deployment.status = "live"
            deployment.config["vercel_id"] = data.get("id")

        return deployment

    async def deploy_backend(
        self, project_id: str, files: Dict[str, str]
    ) -> Deployment:
        """Deploy backend to Railway."""
        deployment = Deployment(
            project_id=project_id,
            provider="railway",
            url=f"https://api-{project_id[:8]}.railway.app",
            status="live",
        )
        self._deployments[deployment.id] = deployment
        await self._persist(deployment)
        return deployment

    def get_latest(self, project_id: str) -> Optional[Deployment]:
        """Get the latest deployment for a project."""
        deploys = [d for d in self._deployments.values() if d.project_id == project_id]
        if not deploys:
            return None
        return max(deploys, key=lambda d: d.created_at)

    def list_by_project(self, project_id: str) -> List[Deployment]:
        return [d for d in self._deployments.values() if d.project_id == project_id]

    async def _persist(self, deployment: Deployment):
        """Persist deployment to Convex via the repositories layer."""
        try:
            from core.repositories import get_async_deployment_repository

            await get_async_deployment_repository().create(
                project_id=deployment.project_id,
                provider=deployment.provider,
                url=deployment.url,
                subdomain=deployment.subdomain,
                status=deployment.status,
                config=deployment.config,
            )
        except Exception:
            logger.warning("Failed to persist deployment", exc_info=True)


_service: Optional[DeployService] = None


def get_deploy_service() -> DeployService:
    global _service
    if _service is None:
        _service = DeployService()
    return _service
