"""
VOS3 Database Layer
====================
Convex-powered persistence for V-Core and all services.
"""

from .convex import get_convex_client, ConvexClient

# Legacy alias for any code still calling get_db()
from .convex import get_db

__all__ = ["get_convex_client", "ConvexClient", "get_db"]
