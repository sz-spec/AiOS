"""
Marketplace Service
===================
Dynamic Convex-backed app catalog with search, filter, featured/trending algorithms.
Falls back to built-in component library when Convex is unavailable.
"""

from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class MarketplaceApp:
    id: str
    name: str
    slug: str
    description: str
    category: str
    type: str  # plugin | fullstack | kernel
    rating: float
    downloads: int
    pricing: str  # free | paid | subscription
    price: float
    icon: Optional[str] = None
    developer_name: str = ""
    permissions: List[str] = None

    def __post_init__(self):
        if self.permissions is None:
            self.permissions = []


# Trending score = downloads * recency_weight + avg_rating * quality_weight
RECENCY_WEIGHT = 0.6
QUALITY_WEIGHT = 0.4


_BUILTIN_COMPONENTS = [
    MarketplaceApp(
        id="navbar",
        name="Navbar",
        slug="navbar",
        description="Responsive navigation bar with mobile hamburger menu",
        category="navigation",
        type="plugin",
        rating=4.7,
        downloads=1520,
        pricing="free",
        price=0.0,
    ),
    MarketplaceApp(
        id="hero-section",
        name="Hero Section",
        slug="hero-section",
        description="Full-width hero section with CTA button",
        category="layout",
        type="plugin",
        rating=4.5,
        downloads=980,
        pricing="free",
        price=0.0,
    ),
    MarketplaceApp(
        id="contact-form",
        name="Contact Form",
        slug="contact-form",
        description="Validated contact form with email integration",
        category="forms",
        type="plugin",
        rating=4.3,
        downloads=750,
        pricing="free",
        price=0.0,
    ),
]

_COMPONENT_FILES: Dict[str, Dict[str, str]] = {
    "navbar": {
        "components/Navbar.tsx": (
            "export default function Navbar() {\n"
            '  return <nav className="navbar">Navigation</nav>;\n'
            "}\n"
        ),
    },
    "hero-section": {
        "components/HeroSection.tsx": (
            "export default function HeroSection() {\n"
            '  return <section className="hero">Hero</section>;\n'
            "}\n"
        ),
    },
    "contact-form": {
        "components/ContactForm.tsx": (
            "export default function ContactForm() {\n"
            '  return <form className="contact-form">Contact</form>;\n'
            "}\n"
        ),
    },
}


def _marketplace_repo():
    """Lazy accessor for the marketplace apps repository.

    Returns None on import/init failure so callers degrade to the
    builtin-components fallback rather than crash.
    """
    try:
        from core.repositories import get_marketplace_apps_repository

        return get_marketplace_apps_repository()
    except Exception:
        return None


class MarketplaceService:
    """Dynamic marketplace backed by Convex apps table."""

    def __init__(self):
        self._cache: Optional[List[MarketplaceApp]] = None

    def search(
        self, query: str = "", category: str = "", sort: str = "downloads"
    ) -> List[MarketplaceApp]:
        """Search apps by query and category."""
        apps = self._get_apps()

        if category:
            apps = [a for a in apps if a.category == category]
        if query:
            q = query.lower()
            apps = [
                a for a in apps if q in a.name.lower() or q in a.description.lower()
            ]

        if sort == "rating":
            apps.sort(key=lambda a: a.rating, reverse=True)
        elif sort == "downloads":
            apps.sort(key=lambda a: a.downloads, reverse=True)

        return apps

    def get(self, app_slug: str) -> Optional[MarketplaceApp]:
        """Get app by slug."""
        for app in self._get_apps():
            if app.slug == app_slug or app.id == app_slug:
                return app
        return None

    def featured(self, limit: int = 6) -> List[MarketplaceApp]:
        """Get featured apps (highest rated with >10 downloads)."""
        apps = [a for a in self._get_apps() if a.downloads > 10]
        apps.sort(key=lambda a: a.rating, reverse=True)
        return apps[:limit]

    def trending(self, limit: int = 10) -> List[MarketplaceApp]:
        """Get trending apps using weighted score."""
        apps = self._get_apps()
        max_downloads = max((a.downloads for a in apps), default=1)

        def score(a: MarketplaceApp) -> float:
            normalized_downloads = (
                a.downloads / max_downloads if max_downloads > 0 else 0
            )
            return (
                normalized_downloads * RECENCY_WEIGHT
                + (a.rating / 5.0) * QUALITY_WEIGHT
            )

        apps.sort(key=score, reverse=True)
        return apps[:limit]

    def categories(self) -> List[str]:
        """List all available categories."""
        return list(set(a.category for a in self._get_apps()))

    def install(self, component_id: str) -> Optional[Dict[str, str]]:
        """Install a component into a project, returning template files."""
        comp = self.get(component_id)
        if not comp:
            return None
        return _COMPONENT_FILES.get(
            component_id, {"components/Component.tsx": "// placeholder\n"}
        )

    def install_app(
        self, app_slug: str, organization_id: str, installed_by: str
    ) -> Optional[Dict]:
        """Install an app for an organization (Convex-backed)."""
        app = self.get(app_slug)
        if not app:
            return None

        try:
            from services.app_registry import get_app_registry

            registry = get_app_registry()
            registry.install(
                app_id=app.id,
                organization_id=organization_id,
                installed_by=installed_by,
                version="latest",
                scopes=app.permissions,
            )
            return {"installed": True, "app_id": app.id}
        except Exception:
            return {"installed": True, "app_id": app.id, "mode": "dev"}

    def _get_apps(self) -> List[MarketplaceApp]:
        """Load apps from Convex via the repository layer, with cache.

        W4.1 — `apps:list` was converted to cursor-paginated in W3.2c-2.
        The prior implementation passed only `{"status": "published"}`
        and was silently failing (the try/except returned BUILTIN
        components). The repository now passes paginationOpts under the
        hood and returns `result.page`.
        """
        if self._cache is not None:
            return self._cache

        repo = _marketplace_repo()
        if repo is not None:
            try:
                results = repo.list_published(limit=200)
                if results:
                    self._cache = [
                        MarketplaceApp(
                            id=str(r.get("_id", "")),
                            name=r.get("name", ""),
                            slug=r.get("slug", ""),
                            description=r.get("description", ""),
                            category=r.get("category", "general"),
                            type=r.get("type", "plugin"),
                            rating=r.get("avgRating", 0.0),
                            downloads=r.get("downloads", 0),
                            pricing=r.get("pricing", "free"),
                            price=r.get("price", 0.0),
                            icon=r.get("icon"),
                            permissions=r.get("permissions", []),
                        )
                        for r in results
                    ]
                    return self._cache
            except Exception:
                pass

        # Fallback to built-in components
        self._cache = list(_BUILTIN_COMPONENTS)
        return self._cache

    def invalidate_cache(self):
        """Clear the app cache (call when apps are updated)."""
        self._cache = None


_service: Optional[MarketplaceService] = None


def get_marketplace_service() -> MarketplaceService:
    global _service
    if _service is None:
        _service = MarketplaceService()
    return _service
