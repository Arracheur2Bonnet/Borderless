"""The live-model tests run only when asked for (`pytest tests/service -m service`): the default run is offline and its
counts do not depend on whether Ollama happens to be running."""


def pytest_ignore_collect(collection_path, config):
    return "service" not in (config.getoption("-m") or "")
