"""Health requests must retain middleware without traversing unrelated routers."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "UNSUPPORTED"])
def test_cold_health_does_not_match_unrelated_router_branches(monkeypatch, method):
    from app import create_app
    application = create_app()
    # Included router branches are BaseRoutes without a concrete path. Their
    # public matches() operation is where lazy dependency schemas are built.
    branches = [route for route in application.routes if not hasattr(route, 'path')]
    assert branches, 'expected included API router branches in the actual app'
    spies = []
    for route in branches:
        spy = Mock(wraps=route.matches)
        monkeypatch.setattr(route, 'matches', spy)
        spies.append(spy)
    client = TestClient(application, base_url='http://localhost')
    response = client.request(method, '/health')
    if method == 'GET':
        assert response.status_code == 200
        assert response.json()['status'] == 'healthy'
        assert isinstance(response.json()['services'], dict)
    else:
        assert response.status_code == 405
        assert response.headers['allow'] == 'GET'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert all(spy.call_count == 0 for spy in spies)
    # The branches remain mounted and reachable through normal routing.
    response = client.get('/no-such-health-routing-control')
    assert response.status_code == 404
    assert any(spy.call_count > 0 for spy in spies)
