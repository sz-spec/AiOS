"""
Socket.io Real-time Integration
================================
Based on r/vbuilder community recommendations.

Features:
- Real-time code generation updates
- Bi-directional communication
- Room-based sessions
- Auto-reconnection
"""

import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timezone

import socketio
from fastapi import FastAPI

# =============================================================================
# Socket.io Server
# =============================================================================


class RealtimeServer:
    """
    Socket.io server for real-time communication.

    Based on community recommendations from r/vbuilder:
    - WebSockets reduce polling by 80%
    - Real-time updates improve UX significantly

    Usage:
        server = RealtimeServer()
        app = server.create_app()

        # Run with uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)
    """

    def __init__(self):
        self.sio = socketio.AsyncServer(
            async_mode="asgi",
            cors_allowed_origins="*",
            logger=True,
            engineio_logger=True,
        )
        self.app = socketio.ASGIApp(self.sio)

        # Track connected clients
        self.sessions: Dict[str, Dict[str, Any]] = {}

        # Register event handlers
        self._register_handlers()

    def _register_handlers(self):
        """Register Socket.io event handlers."""

        @self.sio.event
        async def connect(sid, environ):
            """Handle client connection."""
            print(f"Client connected: {sid}")
            self.sessions[sid] = {
                "connected_at": datetime.now(timezone.utc).isoformat(),
                "project_id": None,
            }
            await self.sio.emit("connected", {"sid": sid}, room=sid)

        @self.sio.event
        async def disconnect(sid):
            """Handle client disconnection."""
            print(f"Client disconnected: {sid}")
            if sid in self.sessions:
                del self.sessions[sid]

        @self.sio.event
        async def join_project(sid, data):
            """Join a project room for real-time updates."""
            project_id = data.get("project_id")
            if project_id:
                await self.sio.enter_room(sid, f"project:{project_id}")
                self.sessions[sid]["project_id"] = project_id
                await self.sio.emit(
                    "joined_project", {"project_id": project_id}, room=sid
                )

        @self.sio.event
        async def leave_project(sid, data):
            """Leave a project room."""
            project_id = data.get("project_id")
            if project_id:
                await self.sio.leave_room(sid, f"project:{project_id}")
                self.sessions[sid]["project_id"] = None

        @self.sio.event
        async def generate_app(sid, data):
            """
            Handle app generation request.
            Streams code generation progress to client.
            """
            prompt = data.get("prompt", "")
            project_id = data.get("project_id")

            # Emit start event
            await self.sio.emit(
                "generation_started",
                {
                    "prompt": prompt,
                    "project_id": project_id,
                },
                room=sid,
            )

            try:
                # Import here to avoid circular imports
                from ai.agents.multi_agent import MultiAgentBuilder

                builder = MultiAgentBuilder()

                # Stream progress
                await self.sio.emit(
                    "generation_progress",
                    {
                        "phase": "architect",
                        "message": "Designing architecture...",
                        "progress": 10,
                    },
                    room=sid,
                )

                # Build project
                result = builder.build(prompt)

                # Emit file updates one by one
                total_files = len(result.files)
                for i, (path, content) in enumerate(result.files.items()):
                    progress = 20 + int((i / total_files) * 70)

                    await self.sio.emit(
                        "file_generated",
                        {
                            "path": path,
                            "content": content,
                            "progress": progress,
                        },
                        room=sid,
                    )

                    # Also emit to project room
                    if project_id:
                        await self.sio.emit(
                            "file_updated",
                            {
                                "project_id": project_id,
                                "path": path,
                                "content": content,
                            },
                            room=f"project:{project_id}",
                        )

                    # Small delay for UX
                    await asyncio.sleep(0.1)

                # Emit completion
                await self.sio.emit(
                    "generation_complete",
                    {
                        "project_id": project_id,
                        "files": list(result.files.keys()),
                        "progress": 100,
                    },
                    room=sid,
                )

            except Exception as e:
                await self.sio.emit(
                    "generation_error",
                    {
                        "error": str(e),
                    },
                    room=sid,
                )

        @self.sio.event
        async def chat_message(sid, data):
            """Handle chat message for incremental updates."""
            message = data.get("message", "")
            project_id = data.get("project_id")

            # Emit typing indicator
            await self.sio.emit(
                "agent_typing",
                {
                    "agent": "assistant",
                },
                room=sid,
            )

            try:
                from ai.agents.router_agent import RouterAgent

                router = RouterAgent()

                # Stream response
                full_response = ""
                async for chunk in router.stream(message, {"project_id": project_id}):
                    if chunk.get("type") == "content":
                        content = chunk.get("content", "")
                        full_response += content

                        await self.sio.emit(
                            "chat_chunk",
                            {
                                "content": content,
                                "agent": chunk.get("agent", "assistant"),
                            },
                            room=sid,
                        )

                    elif chunk.get("type") == "file":
                        await self.sio.emit(
                            "file_updated",
                            {
                                "project_id": project_id,
                                "path": chunk.get("path"),
                                "content": chunk.get("content"),
                            },
                            room=sid,
                        )

                        # Broadcast to project room
                        if project_id:
                            await self.sio.emit(
                                "file_updated",
                                {
                                    "project_id": project_id,
                                    "path": chunk.get("path"),
                                    "content": chunk.get("content"),
                                },
                                room=f"project:{project_id}",
                            )

                # Emit complete message
                await self.sio.emit(
                    "chat_complete",
                    {
                        "message": full_response,
                        "agent": "assistant",
                    },
                    room=sid,
                )

            except Exception as e:
                await self.sio.emit(
                    "chat_error",
                    {
                        "error": str(e),
                    },
                    room=sid,
                )

        @self.sio.event
        async def sync_github(sid, data):
            """Sync project to GitHub."""
            project_id = data.get("project_id")
            files = data.get("files", {})
            repo = data.get("repo")

            await self.sio.emit(
                "github_sync_started",
                {
                    "project_id": project_id,
                },
                room=sid,
            )

            try:
                from tools.github_sync import sync_project_to_github

                result = await sync_project_to_github(
                    files=files, repo=repo, message=f"AI Generated: {len(files)} files"
                )

                await self.sio.emit(
                    "github_sync_complete",
                    {
                        "project_id": project_id,
                        "success": result.success,
                        "commit_sha": result.commit_sha,
                        "files_synced": result.files_synced,
                    },
                    room=sid,
                )

            except Exception as e:
                await self.sio.emit(
                    "github_sync_error",
                    {
                        "error": str(e),
                    },
                    room=sid,
                )

    # -------------------------------------------------------------------------
    # Server Methods
    # -------------------------------------------------------------------------

    async def broadcast_to_project(
        self, project_id: str, event: str, data: Dict[str, Any]
    ):
        """Broadcast event to all clients in a project room."""
        await self.sio.emit(event, data, room=f"project:{project_id}")

    async def notify_file_change(
        self, project_id: str, path: str, content: str, action: str = "update"
    ):
        """Notify all clients about a file change."""
        await self.broadcast_to_project(
            project_id,
            "file_updated",
            {
                "project_id": project_id,
                "path": path,
                "content": content,
                "action": action,
            },
        )

    async def notify_project_status(self, project_id: str, status: str):
        """Notify all clients about project status change."""
        await self.broadcast_to_project(
            project_id,
            "project_status",
            {
                "project_id": project_id,
                "status": status,
            },
        )

    def create_app(self, fastapi_app: FastAPI = None) -> FastAPI:
        """
        Create FastAPI app with Socket.io mounted.

        Args:
            fastapi_app: Existing FastAPI app to mount on

        Returns:
            FastAPI app with Socket.io
        """
        if fastapi_app is None:
            fastapi_app = FastAPI(title="AI App Builder Realtime")

        # Mount Socket.io
        fastapi_app.mount("/socket.io", self.app)

        return fastapi_app


# =============================================================================
# Singleton Instance
# =============================================================================

_realtime_server: Optional[RealtimeServer] = None


def get_realtime_server() -> RealtimeServer:
    """Get the realtime server singleton."""
    global _realtime_server
    if _realtime_server is None:
        _realtime_server = RealtimeServer()
    return _realtime_server


# =============================================================================
# Integration with FastAPI Server
# =============================================================================


def add_realtime_to_app(app: FastAPI) -> FastAPI:
    """
    Add real-time capabilities to an existing FastAPI app.

    Usage:
        from fastapi import FastAPI
        from tools.realtime import add_realtime_to_app

        app = FastAPI()
        app = add_realtime_to_app(app)
    """
    server = get_realtime_server()
    return server.create_app(app)


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    server = RealtimeServer()
    app = server.create_app()

    uvicorn.run(app, host="0.0.0.0", port=8000)
