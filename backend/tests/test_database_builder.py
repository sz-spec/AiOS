"""Tests for database_builder — CRUD scaffolding."""

from services.database_builder import (
    generate_api_routes,
    generate_crud_components,
)


def _sample_tables():
    return [
        {
            "name": "products",
            "columns": [
                {"id": "c1", "name": "product_name", "type": "text"},
                {"id": "c2", "name": "price", "type": "number"},
            ],
        }
    ]


class TestDatabaseBuilder:
    def test_generate_api_routes(self):
        routes = generate_api_routes(_sample_tables())
        assert any("products" in k for k in routes.keys())

    def test_generate_crud_components(self):
        comps = generate_crud_components(_sample_tables())
        assert any("products" in k for k in comps.keys())
