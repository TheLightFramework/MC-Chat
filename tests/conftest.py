import pytest


@pytest.fixture(autouse=True)
def offline_model_capabilities(monkeypatch):
    """Application tests never query the real public model catalog."""
    from backend.reasoning import capabilities
    async def lookup(_):
        return capabilities(None)
    monkeypatch.setattr('backend.app.reasoning_capabilities', lookup)


@pytest.fixture(autouse=True)
def offline_provider_identity(monkeypatch):
    """Normal application tests do not contact routing metadata APIs."""
    async def resolve(model, metadata, key):
        metadata.setdefault('provider_name', 'Test Provider')
        return dict(provider_name=metadata['provider_name'], provider_slug='test-provider', scope='provider', implicit_caching=None)
    monkeypatch.setattr('backend.routing.resolve_provider', resolve)
