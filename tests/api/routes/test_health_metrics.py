from unittest.mock import patch

from src.api.routes.health import _generate_metrics_payload


def test_generate_metrics_payload_uses_default_registry_without_multiproc(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)

    with patch("src.api.routes.health.generate_latest", return_value=b"default") as generate_latest_mock:
        payload = _generate_metrics_payload()

    assert payload == b"default"
    generate_latest_mock.assert_called_once_with()


def test_generate_metrics_payload_uses_multiprocess_registry(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", "/tmp/test-prom")

    with patch("src.api.routes.health.CollectorRegistry") as registry_cls, patch(
        "src.api.routes.health.multiprocess.MultiProcessCollector"
    ) as collector_cls, patch(
        "src.api.routes.health.generate_latest",
        return_value=b"multi",
    ) as generate_latest_mock:
        registry = registry_cls.return_value
        payload = _generate_metrics_payload()

    assert payload == b"multi"
    collector_cls.assert_called_once_with(registry)
    generate_latest_mock.assert_called_once_with(registry)
