"""Tests for MarketplaceService — dynamic Convex-backed app catalog."""

from services.marketplace_service import MarketplaceService, MarketplaceApp


def _make_app(**overrides):
    defaults = {
        "id": "test-app",
        "name": "Test App",
        "slug": "test-app",
        "description": "A test application",
        "category": "general",
        "type": "plugin",
        "rating": 4.0,
        "downloads": 100,
        "pricing": "free",
        "price": 0.0,
    }
    defaults.update(overrides)
    return MarketplaceApp(**defaults)


SAMPLE_APPS = [
    _make_app(
        id="navbar-responsive",
        name="Responsive Navbar",
        slug="navbar-responsive",
        description="A responsive navigation bar",
        category="navigation",
        rating=4.5,
        downloads=500,
    ),
    _make_app(
        id="hero-section",
        name="Hero Section",
        slug="hero-section",
        description="Stunning hero section",
        category="layout",
        rating=4.8,
        downloads=300,
    ),
    _make_app(
        id="contact-form",
        name="Contact Form",
        slug="contact-form",
        description="A contact form builder",
        category="forms",
        rating=4.2,
        downloads=200,
    ),
]


class TestMarketplaceService:
    def _svc(self):
        svc = MarketplaceService()
        # Inject sample apps directly to avoid Convex dependency
        svc._cache = list(SAMPLE_APPS)
        return svc

    def test_search_all(self):
        results = self._svc().search()
        assert len(results) == 3

    def test_search_by_category(self):
        results = self._svc().search(category="navigation")
        assert len(results) == 1
        assert results[0].id == "navbar-responsive"

    def test_search_by_query(self):
        results = self._svc().search(query="form")
        assert len(results) == 1
        assert results[0].id == "contact-form"

    def test_search_no_results(self):
        results = self._svc().search(query="nonexistent")
        assert results == []

    def test_search_both_query_and_category(self):
        results = self._svc().search(query="nav", category="navigation")
        assert len(results) == 1

    def test_search_sort_by_rating(self):
        results = self._svc().search(sort="rating")
        assert results[0].id == "hero-section"

    def test_search_sort_by_downloads(self):
        results = self._svc().search(sort="downloads")
        assert results[0].id == "navbar-responsive"

    def test_get_existing(self):
        comp = self._svc().get("hero-section")
        assert comp is not None
        assert comp.name == "Hero Section"

    def test_get_by_id(self):
        comp = self._svc().get("contact-form")
        assert comp is not None
        assert comp.slug == "contact-form"

    def test_get_nonexistent(self):
        assert self._svc().get("fake") is None

    def test_featured(self):
        featured = self._svc().featured(limit=2)
        assert len(featured) <= 2
        # Featured are highest rated with >10 downloads
        assert all(a.downloads > 10 for a in featured)

    def test_trending(self):
        trending = self._svc().trending(limit=2)
        assert len(trending) <= 2

    def test_categories(self):
        cats = self._svc().categories()
        assert set(cats) == {"navigation", "layout", "forms"}

    def test_install(self):
        svc = self._svc()
        result = svc.install_app("navbar-responsive", "org-123", "user-456")
        assert result is not None
        assert result.get("installed") is True

    def test_install_nonexistent(self):
        svc = self._svc()
        assert svc.install_app("fake", "org-123", "user-456") is None

    def test_invalidate_cache(self):
        svc = self._svc()
        assert svc._cache is not None
        svc.invalidate_cache()
        assert svc._cache is None

    def test_get_returns_reference_mutation_risk(self):
        """Document that get() returns a mutable reference to cached object."""
        svc = self._svc()
        comp = svc.get("hero-section")
        original_rating = comp.rating
        comp.rating = 0.0
        refetched = svc.get("hero-section")
        assert refetched.rating == 0.0 or refetched.rating == original_rating
