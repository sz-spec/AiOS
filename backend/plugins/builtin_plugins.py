"""
Built-in Example Plugins

Demonstrates plugin development patterns:
1. SlackNotificationPlugin - Notification integration
2. GitHubDeployPlugin - Deployment provider
3. OpenAIProviderPlugin - AI model provider
"""

import asyncio
from typing import Optional
from datetime import datetime, timezone

from plugins.plugin_system import (
    BasePlugin,
    PluginManifest,
    HookType,
    HookContext,
    requires_permission,
)

# ============================================
# Slack Notification Plugin
# ============================================


class SlackNotificationPlugin(BasePlugin):
    """
    Sends notifications to Slack channels.

    Configuration:
    - webhook_url: Slack incoming webhook URL
    - default_channel: Default channel for notifications
    - notify_on_deploy: Send notification on deploy
    - notify_on_error: Send notification on errors
    """

    async def initialize(self) -> bool:
        """Initialize the plugin."""
        webhook_url = self.settings.get("webhook_url")
        if not webhook_url:
            self.log("Warning: No webhook_url configured", "warning")

        # Register hooks
        self.register_hook(HookType.AFTER_DEPLOY, self.on_deploy)
        self.register_hook(HookType.ON_DEPLOY_ERROR, self.on_deploy_error)
        self.register_hook(HookType.ON_BUILD_ERROR, self.on_build_error)

        self.log("Slack notification plugin initialized")
        return True

    async def cleanup(self) -> None:
        """Cleanup resources."""
        self.log("Slack plugin cleanup")

    # Hook handlers

    async def on_deploy(self, context: HookContext) -> dict:
        """Handle successful deployment."""
        if not self.settings.get("notify_on_deploy", True):
            return {"skipped": True}

        project_name = context.data.get("project_name", "Unknown")
        deploy_url = context.data.get("deploy_url", "")

        message = (
            f"🚀 *Deployment Successful*\n"
            f"Project: {project_name}\n"
            f"URL: {deploy_url}\n"
            f"Time: {datetime.now(timezone.utc).isoformat()}"
        )

        await self._send_notification(message, "good")
        return {"sent": True, "message": message}

    async def on_deploy_error(self, context: HookContext) -> dict:
        """Handle deployment error."""
        if not self.settings.get("notify_on_error", True):
            return {"skipped": True}

        project_name = context.data.get("project_name", "Unknown")
        error = context.data.get("error", "Unknown error")

        message = (
            f"❌ *Deployment Failed*\n" f"Project: {project_name}\n" f"Error: {error}"
        )

        await self._send_notification(message, "danger")
        return {"sent": True, "message": message}

    async def on_build_error(self, context: HookContext) -> dict:
        """Handle build error."""
        if not self.settings.get("notify_on_error", True):
            return {"skipped": True}

        project_name = context.data.get("project_name", "Unknown")
        error = context.data.get("error", "Unknown error")

        message = f"⚠️ *Build Failed*\n" f"Project: {project_name}\n" f"Error: {error}"

        await self._send_notification(message, "warning")
        return {"sent": True, "message": message}

    # Internal methods

    async def _send_notification(self, message: str, color: str = "good") -> bool:
        """Send notification to Slack."""
        webhook_url = self.settings.get("webhook_url")
        if not webhook_url:
            self.log("No webhook URL configured", "error")
            return False

        self.settings.get("default_channel", "#general")

        # In production, use aiohttp to send
        self.log(f"Would send to Slack: {message[:50]}...")
        return True

    # Public API

    @requires_permission("notifications:send")
    async def send_custom_message(
        self,
        message: str,
        channel: Optional[str] = None,
    ) -> bool:
        """Send a custom message to Slack."""
        return await self._send_notification(message)


# ============================================
# GitHub Deploy Plugin
# ============================================


class GitHubDeployPlugin(BasePlugin):
    """
    Deploys projects to GitHub Pages.

    Configuration:
    - github_token: GitHub personal access token
    - default_branch: Default branch to deploy from
    - auto_deploy: Enable automatic deployment on build
    """

    async def initialize(self) -> bool:
        """Initialize the plugin."""
        token = self.settings.get("github_token")
        if not token:
            self.log("Warning: No github_token configured", "warning")

        # Register hooks
        self.register_hook(HookType.AFTER_BUILD, self.on_build_complete)
        self.register_hook(HookType.BEFORE_DEPLOY, self.before_deploy)

        self.log("GitHub Deploy plugin initialized")
        return True

    async def on_build_complete(self, context: HookContext) -> dict:
        """Handle build completion."""
        if not self.settings.get("auto_deploy", False):
            return {"auto_deploy": False}

        project_id = context.project_id
        build_output = context.data.get("build_output", "dist")

        self.log(f"Auto-deploying project {project_id}")

        result = await self.deploy(
            project_id=project_id,
            build_output=build_output,
        )

        return result

    async def before_deploy(self, context: HookContext) -> dict:
        """Pre-deploy validation."""
        # Validate GitHub token
        if not self.settings.get("github_token"):
            return {"valid": False, "error": "GitHub token not configured"}

        # Check repo exists
        repo = context.data.get("repo")
        if not repo:
            return {"valid": False, "error": "Repository not specified"}

        return {"valid": True}

    # Public API

    @requires_permission("deploy:github")
    async def deploy(
        self,
        project_id: str,
        build_output: str = "dist",
        branch: Optional[str] = None,
    ) -> dict:
        """Deploy to GitHub Pages."""
        token = self.settings.get("github_token")
        if not token:
            return {"success": False, "error": "GitHub token not configured"}

        deploy_branch = branch or self.settings.get("default_branch", "gh-pages")

        self.log(f"Deploying {project_id} to branch {deploy_branch}")

        # In production: use GitHub API
        # 1. Create/update branch
        # 2. Push build files
        # 3. Enable GitHub Pages

        return {
            "success": True,
            "branch": deploy_branch,
            "url": f"https://user.github.io/{project_id}",
        }

    async def get_deployment_status(self, project_id: str) -> dict:
        """Get deployment status from GitHub."""
        return {
            "status": "deployed",
            "url": f"https://user.github.io/{project_id}",
            "last_deployed": datetime.now(timezone.utc).isoformat(),
        }


# ============================================
# OpenAI Provider Plugin
# ============================================


class OpenAIProviderPlugin(BasePlugin):
    """
    AI model provider using OpenAI API.

    Configuration:
    - api_key: OpenAI API key
    - model: Default model (gpt-4, gpt-3.5-turbo)
    - max_tokens: Max tokens for completion
    - temperature: Temperature setting
    """

    async def initialize(self) -> bool:
        """Initialize the plugin."""
        api_key = self.settings.get("api_key")
        if not api_key:
            self.log("Warning: No api_key configured", "warning")

        # Register hooks
        self.register_hook(HookType.BEFORE_AI_GENERATE, self.before_generate)
        self.register_hook(HookType.ON_AI_ERROR, self.on_ai_error)

        self.log("OpenAI Provider plugin initialized")
        return True

    async def before_generate(self, context: HookContext) -> dict:
        """Pre-generation hook for request modification."""
        # Can modify the request before it's sent
        prompt = context.data.get("prompt", "")

        # Add system context if needed
        enhanced_prompt = prompt

        return {
            "modified": True,
            "original_prompt": prompt,
            "enhanced_prompt": enhanced_prompt,
        }

    async def on_ai_error(self, context: HookContext) -> dict:
        """Handle AI generation errors."""
        error = context.data.get("error", "")

        # Implement retry logic or fallback
        if "rate_limit" in error.lower():
            self.log("Rate limited, implementing backoff", "warning")
            await asyncio.sleep(1)
            return {"retry": True, "delay": 1}

        return {"retry": False}

    # Public API

    @requires_permission("ai:generate")
    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> dict:
        """Generate completion using OpenAI."""
        api_key = self.settings.get("api_key")
        if not api_key:
            return {"success": False, "error": "API key not configured"}

        model = model or self.settings.get("model", "gpt-4")
        max_tokens = max_tokens or self.settings.get("max_tokens", 2000)
        temperature = temperature or self.settings.get("temperature", 0.7)

        self.log(f"Generating with model {model}")

        # In production: use openai library
        # response = await openai.ChatCompletion.create(...)

        return {
            "success": True,
            "model": model,
            "completion": f"[Mock completion for: {prompt[:50]}...]",
            "usage": {
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": 100,
                "total_tokens": len(prompt.split()) + 100,
            },
        }

    async def get_available_models(self) -> list[dict]:
        """Get available OpenAI models."""
        return [
            {"id": "gpt-4", "name": "GPT-4", "max_tokens": 8192},
            {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "max_tokens": 128000},
            {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "max_tokens": 4096},
        ]


# ============================================
# Webhook Integration Plugin
# ============================================


class WebhookPlugin(BasePlugin):
    """
    Generic webhook integration for external services.

    Configuration:
    - webhooks: List of webhook configurations
      - url: Webhook URL
      - events: List of events to trigger on
      - secret: Optional secret for signing
    """

    async def initialize(self) -> bool:
        """Initialize the plugin."""
        webhooks = self.settings.get("webhooks", [])

        if not webhooks:
            self.log("No webhooks configured", "warning")

        # Register all hooks
        for hook_type in HookType:
            self.register_hook(hook_type, self._create_handler(hook_type))

        self.log(f"Webhook plugin initialized with {len(webhooks)} webhooks")
        return True

    def _create_handler(self, hook_type: HookType):
        """Create a handler for a specific hook type."""

        async def handler(context: HookContext) -> dict:
            return await self._handle_event(hook_type, context)

        return handler

    async def _handle_event(
        self,
        hook_type: HookType,
        context: HookContext,
    ) -> dict:
        """Handle an event and send to matching webhooks."""
        webhooks = self.settings.get("webhooks", [])
        results = []

        for webhook in webhooks:
            events = webhook.get("events", [])
            if hook_type.value in events or "*" in events:
                result = await self._send_webhook(
                    url=webhook["url"],
                    event=hook_type.value,
                    data=context.data,
                    secret=webhook.get("secret"),
                )
                results.append(result)

        return {
            "webhooks_triggered": len(results),
            "results": results,
        }

    async def _send_webhook(
        self,
        url: str,
        event: str,
        data: dict,
        secret: Optional[str] = None,
    ) -> dict:
        """Send webhook request."""
        {
            "event": event,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # In production: use aiohttp with HMAC signing
        self.log(f"Sending webhook to {url} for event {event}")

        return {
            "url": url,
            "event": event,
            "success": True,
        }


# ============================================
# Plugin Manifests for Built-ins
# ============================================

BUILTIN_MANIFESTS = {
    "slack-notifications": PluginManifest(
        id="slack-notifications",
        name="Slack Notifications",
        version="1.0.0",
        description="Send build and deploy notifications to Slack channels",
        author="VBuilder Team",
        type="notification",
        permissions=["notifications:send"],
        hooks=[
            "after_deploy",
            "on_deploy_error",
            "on_build_error",
        ],
        config_schema={
            "type": "object",
            "properties": {
                "webhook_url": {
                    "type": "string",
                    "title": "Webhook URL",
                    "description": "Slack incoming webhook URL",
                },
                "default_channel": {
                    "type": "string",
                    "title": "Default Channel",
                    "default": "#general",
                },
                "notify_on_deploy": {
                    "type": "boolean",
                    "title": "Notify on Deploy",
                    "default": True,
                },
                "notify_on_error": {
                    "type": "boolean",
                    "title": "Notify on Error",
                    "default": True,
                },
            },
            "required": ["webhook_url"],
        },
    ),
    "github-deploy": PluginManifest(
        id="github-deploy",
        name="GitHub Deploy",
        version="1.0.0",
        description="Deploy projects to GitHub Pages",
        author="VBuilder Team",
        type="deployment",
        permissions=["deploy:github"],
        hooks=[
            "after_build",
            "before_deploy",
        ],
        config_schema={
            "type": "object",
            "properties": {
                "github_token": {
                    "type": "string",
                    "title": "GitHub Token",
                    "description": "Personal access token with repo scope",
                },
                "default_branch": {
                    "type": "string",
                    "title": "Deploy Branch",
                    "default": "gh-pages",
                },
                "auto_deploy": {
                    "type": "boolean",
                    "title": "Auto Deploy",
                    "description": "Automatically deploy on build",
                    "default": False,
                },
            },
            "required": ["github_token"],
        },
    ),
    "openai-provider": PluginManifest(
        id="openai-provider",
        name="OpenAI Provider",
        version="1.0.0",
        description="Use OpenAI GPT models for code generation",
        author="VBuilder Team",
        type="ai_provider",
        permissions=["ai:generate"],
        hooks=[
            "before_ai_generate",
            "on_ai_error",
        ],
        config_schema={
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "title": "API Key",
                    "description": "OpenAI API key",
                },
                "model": {
                    "type": "string",
                    "title": "Model",
                    "enum": ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"],
                    "default": "gpt-4",
                },
                "max_tokens": {
                    "type": "integer",
                    "title": "Max Tokens",
                    "default": 2000,
                },
                "temperature": {
                    "type": "number",
                    "title": "Temperature",
                    "minimum": 0,
                    "maximum": 2,
                    "default": 0.7,
                },
            },
            "required": ["api_key"],
        },
    ),
}


# ============================================
# Plugin Class Registry
# ============================================

PLUGIN_CLASSES = {
    "slack-notifications": SlackNotificationPlugin,
    "github-deploy": GitHubDeployPlugin,
    "openai-provider": OpenAIProviderPlugin,
    "webhook": WebhookPlugin,
}


__all__ = [
    "SlackNotificationPlugin",
    "GitHubDeployPlugin",
    "OpenAIProviderPlugin",
    "WebhookPlugin",
    "BUILTIN_MANIFESTS",
    "PLUGIN_CLASSES",
]
