import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPORTER_PATH = ROOT / "k8s" / "dev" / "metrics_server_exporter.py"
SPEC = importlib.util.spec_from_file_location("metrics_server_exporter", EXPORTER_PATH)
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


class MetricsServerExporterTest(unittest.TestCase):
    def test_cpu_and_memory_quantities_are_converted_to_base_units(self):
        self.assertEqual(0.25, exporter.parse_quantity("250m", "cpu"))
        self.assertEqual(0.5, exporter.parse_quantity("500000u", "cpu"))
        self.assertEqual(1.5, exporter.parse_quantity("1.5", "cpu"))
        self.assertEqual(128 * 1024**2, exporter.parse_quantity("128Mi", "memory"))
        self.assertEqual(1_000_000_000, exporter.parse_quantity("1G", "memory"))

    def test_unknown_or_malformed_quantities_are_rejected(self):
        for value, resource in (("2widgets", "cpu"), ("NaN", "memory"), ("", "cpu")):
            with self.subTest(value=value, resource=resource):
                with self.assertRaises(ValueError):
                    exporter.parse_quantity(value, resource)

    def test_api_payloads_become_pod_and_node_series(self):
        pods = {
            "items": [
                {
                    "metadata": {"namespace": "apps", "name": "web"},
                    "containers": [
                        {
                            "name": "frontend",
                            "usage": {"cpu": "25m", "memory": "32Mi"},
                        }
                    ],
                }
            ]
        }
        nodes = {
            "items": [
                {
                    "metadata": {"name": "node-a"},
                    "usage": {"cpu": "700m", "memory": "2Gi"},
                }
            ]
        }

        metrics = exporter.collect_metrics(pods, nodes)

        self.assertEqual(
            0.025,
            metrics["metrics_server_pod_cpu_cores"][
                ("apps", "web", "frontend")
            ],
        )
        self.assertEqual(
            32 * 1024**2,
            metrics["metrics_server_pod_memory_bytes"][
                ("apps", "web", "frontend")
            ],
        )
        self.assertEqual(
            0.7,
            metrics["metrics_server_node_cpu_cores"][("node-a",)],
        )
        self.assertEqual(
            2 * 1024**3,
            metrics["metrics_server_node_memory_bytes"][("node-a",)],
        )

    def test_metrics_output_is_prometheus_format_and_escapes_labels(self):
        text = exporter.render_metrics(
            {
                "metrics_server_pod_cpu_cores": {
                    ('apps"team', "web\\api", "front\nend"): 0.125
                }
            },
            up=False,
            last_success=123.5,
        )

        self.assertIn(
            'metrics_server_pod_cpu_cores{namespace="apps\\"team",pod="web\\\\api",container="front\\nend"} 0.125',
            text,
        )
        self.assertIn("metrics_server_exporter_up 0", text)
        self.assertIn("metrics_server_exporter_last_success_timestamp_seconds 123.5", text)


if __name__ == "__main__":
    unittest.main()
