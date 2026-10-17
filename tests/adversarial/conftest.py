import pytest


@pytest.fixture
def fresh():
    """Factory: fresh(poisoned_fx=False) -> (orchestrator, session_id). Imports lazily."""
    from tests.adversarial.helpers import fresh as _fresh
    return _fresh
