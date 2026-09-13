"""
Vercel Integration for BMAD
============================

Deploy projects to Vercel with:
- Project creation and configuration
- Deployment management
- Environment variable handling
- Rollback support
"""

import os
import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel

# =============================================================================
# Configuration
# =============================================================================

VERCEL_API_URL = "https://api.vercel.com"


# =============================================================================
# Models
# =============================================================================


class DeploymentConfig(BaseModel):
    """Configuration for a Vercel deployment."""

    project_name: str
    framework: str = "nextjs"
    build_command: Optional[str] = None
    output_directory: Optional[str] = None
    install_command: Optional[str] = None
    environment: Dict[str, str] = {}
    root_directory: Optional[str] = None


class DeploymentResult(BaseModel):
    """Result of a deployment operation."""

    success: bool
    deployment_id: Optional[str] = None
    url: Optional[str] = None
    state: str = "unknown"
    error: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class VercelConfig:
    """Vercel API configuration."""

    token: str
    team_id: Optional[str] = None

    @classmethod
    def from_env(cls) -> "VercelConfig":
        return cls(
            token=os.getenv("VERCEL_TOKEN", ""),
            team_id=os.getenv("VERCEL_TEAM_ID"),
        )


# =============================================================================
# Vercel Service
# =============================================================================


class VercelService:
    """
    Service for deploying projects to Vercel.

    Usage:
        vercel = VercelService(token="...")
        result = await vercel.deploy(
            project_name="my-app",
            files={"src/App.tsx": "...", "package.json": "..."}
        )
    """

    def __init__(self, token: str = None, team_id: str = None):
        config = VercelConfig.from_env()
        self.token = token or config.token
        self.team_id = team_id or config.team_id

        self.client = httpx.AsyncClient(
            base_url=VERCEL_API_URL,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()

    def _add_team_param(self, params: Dict = None) -> Dict:
        """Add team ID to request parameters if set."""
        params = params or {}
        if self.team_id:
            params["teamId"] = self.team_id
        return params

    # -------------------------------------------------------------------------
    # Project Operations
    # -------------------------------------------------------------------------

    async def get_project(self, project_name: str) -> Optional[Dict[str, Any]]:
        """Get project by name."""
        try:
            response = await self.client.get(
                f"/v9/projects/{project_name}", params=self._add_team_param()
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def create_project(
        self, name: str, framework: str = "nextjs", git_repo: str = None
    ) -> Dict[str, Any]:
        """Create a new Vercel project."""
        data = {
            "name": name,
            "framework": framework,
        }

        if git_repo:
            data["gitRepository"] = {
                "type": "github",
                "repo": git_repo,
            }

        response = await self.client.post(
            "/v10/projects", json=data, params=self._add_team_param()
        )
        response.raise_for_status()
        return response.json()

    async def get_or_create_project(
        self, name: str, framework: str = "nextjs"
    ) -> Dict[str, Any]:
        """Get existing project or create new one."""
        project = await self.get_project(name)
        if project:
            return project
        return await self.create_project(name, framework)

    async def update_project(
        self, project_id: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update project settings."""
        response = await self.client.patch(
            f"/v9/projects/{project_id}", json=updates, params=self._add_team_param()
        )
        response.raise_for_status()
        return response.json()

    async def delete_project(self, project_id: str) -> bool:
        """Delete a project."""
        response = await self.client.delete(
            f"/v9/projects/{project_id}", params=self._add_team_param()
        )
        return response.status_code == 204

    # -------------------------------------------------------------------------
    # Environment Variables
    # -------------------------------------------------------------------------

    async def get_env_vars(self, project_id: str) -> List[Dict[str, Any]]:
        """Get project environment variables."""
        response = await self.client.get(
            f"/v9/projects/{project_id}/env", params=self._add_team_param()
        )
        response.raise_for_status()
        return response.json().get("envs", [])

    async def set_env_var(
        self, project_id: str, key: str, value: str, target: List[str] = None
    ) -> Dict[str, Any]:
        """Set an environment variable."""
        target = target or ["production", "preview", "development"]

        response = await self.client.post(
            f"/v10/projects/{project_id}/env",
            json={
                "key": key,
                "value": value,
                "target": target,
                "type": "encrypted",
            },
            params=self._add_team_param(),
        )
        response.raise_for_status()
        return response.json()

    async def set_env_vars(
        self, project_id: str, env_vars: Dict[str, str], target: List[str] = None
    ) -> List[Dict[str, Any]]:
        """Set multiple environment variables."""
        results = []
        for key, value in env_vars.items():
            result = await self.set_env_var(project_id, key, value, target)
            results.append(result)
        return results

    async def delete_env_var(self, project_id: str, env_id: str) -> bool:
        """Delete an environment variable."""
        response = await self.client.delete(
            f"/v9/projects/{project_id}/env/{env_id}", params=self._add_team_param()
        )
        return response.status_code == 200

    # -------------------------------------------------------------------------
    # Deployments
    # -------------------------------------------------------------------------

    async def deploy(
        self, project_name: str, files: Dict[str, str], config: DeploymentConfig = None
    ) -> DeploymentResult:
        """
        Deploy files to Vercel.

        Args:
            project_name: Name of the project
            files: Dict of file paths to content
            config: Optional deployment configuration

        Returns:
            DeploymentResult with deployment status
        """
        try:
            # Ensure project exists
            await self.get_or_create_project(
                project_name, config.framework if config else "nextjs"
            )

            # Prepare files for deployment
            deployment_files = []
            for path, content in files.items():
                deployment_files.append(
                    {
                        "file": path,
                        "data": content,
                    }
                )

            # Create deployment
            deploy_data = {
                "name": project_name,
                "files": deployment_files,
                "projectSettings": {
                    "framework": config.framework if config else "nextjs",
                },
            }

            if config:
                if config.build_command:
                    deploy_data["projectSettings"][
                        "buildCommand"
                    ] = config.build_command
                if config.output_directory:
                    deploy_data["projectSettings"][
                        "outputDirectory"
                    ] = config.output_directory
                if config.install_command:
                    deploy_data["projectSettings"][
                        "installCommand"
                    ] = config.install_command
                if config.root_directory:
                    deploy_data["projectSettings"][
                        "rootDirectory"
                    ] = config.root_directory

            response = await self.client.post(
                "/v13/deployments", json=deploy_data, params=self._add_team_param()
            )
            response.raise_for_status()

            result = response.json()

            return DeploymentResult(
                success=True,
                deployment_id=result.get("id"),
                url=f"https://{result.get('url')}" if result.get("url") else None,
                state=result.get("readyState", "QUEUED"),
                created_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            return DeploymentResult(
                success=False,
                error=str(e),
            )

    async def get_deployment(self, deployment_id: str) -> Dict[str, Any]:
        """Get deployment details."""
        response = await self.client.get(
            f"/v13/deployments/{deployment_id}", params=self._add_team_param()
        )
        response.raise_for_status()
        return response.json()

    async def get_deployment_status(self, deployment_id: str) -> str:
        """Get deployment status."""
        deployment = await self.get_deployment(deployment_id)
        return deployment.get("readyState", "UNKNOWN")

    async def wait_for_deployment(
        self, deployment_id: str, timeout_seconds: int = 300, poll_interval: int = 5
    ) -> DeploymentResult:
        """
        Wait for deployment to complete.

        Args:
            deployment_id: The deployment ID
            timeout_seconds: Maximum wait time
            poll_interval: Time between status checks

        Returns:
            DeploymentResult with final status
        """
        start_time = asyncio.get_event_loop().time()

        while True:
            deployment = await self.get_deployment(deployment_id)
            state = deployment.get("readyState", "UNKNOWN")

            if state == "READY":
                return DeploymentResult(
                    success=True,
                    deployment_id=deployment_id,
                    url=f"https://{deployment.get('url')}",
                    state=state,
                )

            if state in ["ERROR", "CANCELED"]:
                return DeploymentResult(
                    success=False,
                    deployment_id=deployment_id,
                    state=state,
                    error=deployment.get("errorMessage", "Deployment failed"),
                )

            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout_seconds:
                return DeploymentResult(
                    success=False,
                    deployment_id=deployment_id,
                    state=state,
                    error=f"Deployment timeout after {timeout_seconds}s",
                )

            await asyncio.sleep(poll_interval)

    async def list_deployments(
        self, project_name: str = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """List deployments."""
        params = self._add_team_param({"limit": limit})

        if project_name:
            params["projectId"] = project_name

        response = await self.client.get("/v6/deployments", params=params)
        response.raise_for_status()
        return response.json().get("deployments", [])

    async def cancel_deployment(self, deployment_id: str) -> bool:
        """Cancel a deployment."""
        response = await self.client.patch(
            f"/v12/deployments/{deployment_id}/cancel", params=self._add_team_param()
        )
        return response.status_code == 200

    # -------------------------------------------------------------------------
    # Rollback
    # -------------------------------------------------------------------------

    async def rollback(
        self, project_name: str, deployment_id: str = None
    ) -> DeploymentResult:
        """
        Rollback to a previous deployment.

        Args:
            project_name: Name of the project
            deployment_id: Specific deployment to rollback to (or previous if None)

        Returns:
            DeploymentResult with rollback status
        """
        try:
            if not deployment_id:
                # Get previous successful deployment
                deployments = await self.list_deployments(project_name)
                successful = [d for d in deployments if d.get("readyState") == "READY"][
                    1:
                ]  # Skip current

                if not successful:
                    return DeploymentResult(
                        success=False,
                        error="No previous deployment to rollback to",
                    )

                deployment_id = successful[0]["uid"]

            # Create alias to the previous deployment
            project = await self.get_project(project_name)
            if not project:
                return DeploymentResult(
                    success=False,
                    error=f"Project not found: {project_name}",
                )

            # Set the deployment as production
            response = await self.client.post(
                f"/v10/projects/{project['id']}/alias",
                json={
                    "alias": project.get("alias", [{}])[0].get(
                        "domain", f"{project_name}.vercel.app"
                    ),
                    "deploymentId": deployment_id,
                },
                params=self._add_team_param(),
            )
            response.raise_for_status()

            deployment = await self.get_deployment(deployment_id)

            return DeploymentResult(
                success=True,
                deployment_id=deployment_id,
                url=f"https://{deployment.get('url')}",
                state="READY",
            )

        except Exception as e:
            return DeploymentResult(
                success=False,
                error=str(e),
            )

    # -------------------------------------------------------------------------
    # Domains
    # -------------------------------------------------------------------------

    async def get_domains(self, project_id: str) -> List[Dict[str, Any]]:
        """Get project domains."""
        response = await self.client.get(
            f"/v9/projects/{project_id}/domains", params=self._add_team_param()
        )
        response.raise_for_status()
        return response.json().get("domains", [])

    async def add_domain(self, project_id: str, domain: str) -> Dict[str, Any]:
        """Add a domain to the project."""
        response = await self.client.post(
            f"/v10/projects/{project_id}/domains",
            json={"name": domain},
            params=self._add_team_param(),
        )
        response.raise_for_status()
        return response.json()


# =============================================================================
# Convenience Functions
# =============================================================================


async def deploy_to_vercel(
    project_name: str,
    files: Dict[str, str],
    token: str = None,
    wait: bool = True,
    config: DeploymentConfig = None,
) -> DeploymentResult:
    """
    Convenience function to deploy files to Vercel.

    Args:
        project_name: Name of the project
        files: Dict of file paths to content
        token: Optional Vercel API token
        wait: Whether to wait for deployment to complete
        config: Optional deployment configuration

    Returns:
        DeploymentResult with deployment status
    """
    service = VercelService(token=token)

    try:
        result = await service.deploy(project_name, files, config)

        if result.success and wait and result.deployment_id:
            result = await service.wait_for_deployment(result.deployment_id)

        return result
    finally:
        await service.close()


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":

    async def main():
        # Example deployment
        files = {
            "package.json": '{"name": "test-app", "version": "1.0.0"}',
            "index.html": "<html><body>Hello World</body></html>",
        }

        result = await deploy_to_vercel(
            project_name="test-app",
            files=files,
            config=DeploymentConfig(
                project_name="test-app",
                framework="static",
            ),
        )

        print(f"Success: {result.success}")
        print(f"URL: {result.url}")
        if result.error:
            print(f"Error: {result.error}")

    asyncio.run(main())
