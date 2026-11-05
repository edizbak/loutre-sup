#!/usr/bin/env python3
import json
import math
import os
import re
import ssl
import sys
import threading
import time
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import Request, urlopen


API_SERVER = os.environ.get(
    "KUBERNETES_API_SERVER", "https://kubernetes.default.svc"
).rstrip("/")
API_TIMEOUT_SECONDS = 8
POLL_INTERVAL_SECONDS = 30
SERVICE_ACCOUNT_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
METRIC_DEFINITIONS = {
    "metrics_server_pod_cpu_cores": (
        "Current pod container CPU usage in cores reported by Metrics Server.",
        ("namespace", "pod", "container"),
    ),
    "metrics_server_pod_memory_bytes": (
        "Current pod container memory usage in bytes reported by Metrics Server.",
        ("namespace", "pod", "container"),
    ),
    "metrics_server_node_cpu_cores": (
        "Current node CPU usage in cores reported by Metrics Server.",
        ("node",),
    ),
    "metrics_server_node_memory_bytes": (
        "Current node memory usage in bytes reported by Metrics Server.",
        ("node",),
    ),
}
QUANTITY_PATTERN = re.compile(
    r"^([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)([a-zA-Z]*)$"
)
BINARY_MULTIPLIERS = {
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
    "Pi": 1024**5,
    "Ei": 1024**6,
}
DECIMAL_MULTIPLIERS = {
    "n": Decimal("1e-9"),
    "u": Decimal("1e-6"),
    "m": Decimal("1e-3"),
    "k": Decimal("1e3"),
    "K": Decimal("1e3"),
    "M": Decimal("1e6"),
    "G": Decimal("1e9"),
    "T": Decimal("1e12"),
    "P": Decimal("1e15"),
    "E": Decimal("1e18"),
}
state_lock = threading.Lock()
metrics_state = {"metrics": {}, "up": False, "last_success": 0.0}


def parse_quantity(value, resource):
    match = QUANTITY_PATTERN.fullmatch(value)
    if not match:
        raise ValueError(f"invalid {resource} quantity")

    try:
        number = Decimal(match.group(1))
    except InvalidOperation as error:
        raise ValueError(f"invalid {resource} quantity") from error

    suffix = match.group(2)
    if suffix in BINARY_MULTIPLIERS:
        multiplier = Decimal(BINARY_MULTIPLIERS[suffix])
    else:
        multiplier = DECIMAL_MULTIPLIERS.get(suffix)
        if multiplier is None:
            if suffix:
                raise ValueError(f"unsupported {resource} quantity suffix")
            multiplier = Decimal(1)

    result = number * multiplier
    if not result.is_finite() or result < 0:
        raise ValueError(f"{resource} quantity must be finite and non-negative")
    converted = float(result)
    if not math.isfinite(converted):
        raise ValueError(f"{resource} quantity is out of range")
    return converted


def collect_metrics(pod_payload, node_payload):
    metrics = {name: {} for name in METRIC_DEFINITIONS}
    for pod in pod_payload["items"]:
        metadata = pod["metadata"]
        namespace = metadata["namespace"]
        pod_name = metadata["name"]
        for container in pod["containers"]:
            labels = (namespace, pod_name, container["name"])
            usage = container["usage"]
            metrics["metrics_server_pod_cpu_cores"][labels] = parse_quantity(
                usage["cpu"], "cpu"
            )
            metrics["metrics_server_pod_memory_bytes"][labels] = parse_quantity(
                usage["memory"], "memory"
            )

    for node in node_payload["items"]:
        labels = (node["metadata"]["name"],)
        usage = node["usage"]
        metrics["metrics_server_node_cpu_cores"][labels] = parse_quantity(
            usage["cpu"], "cpu"
        )
        metrics["metrics_server_node_memory_bytes"][labels] = parse_quantity(
            usage["memory"], "memory"
        )
    return metrics


def escape_label(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render_metrics(metrics, up, last_success):
    lines = []
    for name, (help_text, label_names) in METRIC_DEFINITIONS.items():
        lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} gauge"))
        for labels, value in metrics.get(name, {}).items():
            label_text = ",".join(
                f'{label}="{escape_label(label_value)}"'
                for label, label_value in zip(label_names, labels)
            )
            lines.append(f"{name}{{{label_text}}} {value:.15g}")
    lines.extend(
        (
            "# HELP metrics_server_exporter_up Whether the last Metrics API poll succeeded.",
            "# TYPE metrics_server_exporter_up gauge",
            f"metrics_server_exporter_up {1 if up else 0}",
            "# HELP metrics_server_exporter_last_success_timestamp_seconds Unix timestamp of the last successful Metrics API poll.",
            "# TYPE metrics_server_exporter_last_success_timestamp_seconds gauge",
            f"metrics_server_exporter_last_success_timestamp_seconds {last_success:.15g}",
        )
    )
    return "\n".join(lines) + "\n"


def fetch_json(path):
    with open(os.path.join(SERVICE_ACCOUNT_DIR, "token"), encoding="utf-8") as token_file:
        token = token_file.read().strip()
    request = Request(
        f"{API_SERVER}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    tls_context = ssl.create_default_context(
        cafile=os.path.join(SERVICE_ACCOUNT_DIR, "ca.crt")
    )
    with urlopen(
        request, timeout=API_TIMEOUT_SECONDS, context=tls_context
    ) as response:
        return json.load(response)


def poll_metrics():
    pods = fetch_json("/apis/metrics.k8s.io/v1beta1/pods")
    nodes = fetch_json("/apis/metrics.k8s.io/v1beta1/nodes")
    return collect_metrics(pods, nodes)


def poll_forever():
    while True:
        try:
            metrics = poll_metrics()
        except (OSError, ValueError, KeyError, TypeError, URLError) as error:
            with state_lock:
                metrics_state.update(metrics={}, up=False)
            print(f"Metrics API poll failed: {error}", file=sys.stderr, flush=True)
        else:
            with state_lock:
                metrics_state.update(
                    metrics=metrics, up=True, last_success=time.time()
                )
        time.sleep(POLL_INTERVAL_SECONDS)


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/healthz", "/readyz"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if self.path != "/metrics":
            self.send_error(404)
            return

        with state_lock:
            snapshot = dict(metrics_state)
        body = render_metrics(
            snapshot["metrics"], snapshot["up"], snapshot["last_success"]
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        print(
            f"{self.address_string()} - {format_string % args}",
            file=sys.stderr,
            flush=True,
        )


if __name__ == "__main__":
    threading.Thread(target=poll_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 9100), MetricsHandler).serve_forever()
