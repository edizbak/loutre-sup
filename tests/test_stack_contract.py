import unittest
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
STACK_DIR = ROOT / "k8s" / "dev"
NAMESPACE_DIR = STACK_DIR / "namespace"
BOOTSTRAP_DIR = STACK_DIR / "bootstrap"
EXPECTED_NAMESPACE = "monitoring-dev"
READ_ONLY_VERBS = {"get", "list", "watch"}


def load_documents(directory):
    documents = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name == "kustomization.yaml":
            continue
        with path.open(encoding="utf-8") as manifest:
            documents.extend(
                (path.name, document)
                for document in yaml.safe_load_all(manifest)
                if document
            )
    return documents


class StackContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (STACK_DIR / "kustomization.yaml").open(encoding="utf-8") as manifest:
            cls.kustomization = yaml.safe_load(manifest)
        cls.documents = load_documents(STACK_DIR)
        cls.namespace_documents = load_documents(NAMESPACE_DIR)
        cls.bootstrap_documents = load_documents(BOOTSTRAP_DIR)
        cls.objects = [document for _, document in cls.documents]
        cls.objects.extend(document for _, document in cls.namespace_documents)
        cls.objects.extend(document for _, document in cls.bootstrap_documents)
        cls.by_kind = {}
        for document in cls.objects:
            cls.by_kind.setdefault(document["kind"], []).append(document)

    def test_stack_has_core_services(self):
        deployments = {
            container["name"]
            for deployment in self.by_kind.get("Deployment", [])
            for container in deployment["spec"]["template"]["spec"]["containers"]
        }
        self.assertTrue(
            {"prometheus", "grafana", "kube-state-metrics"}.issubset(deployments)
        )

    def test_all_objects_are_scoped_to_the_dev_namespace(self):
        self.assertEqual(EXPECTED_NAMESPACE, self.kustomization["namespace"])
        self.assertTrue(self.documents)
        for _, document in self.documents:
            namespace = document["metadata"].get("namespace")
            self.assertIn(namespace, (None, EXPECTED_NAMESPACE))
        namespace = self.namespace_documents[0][1]
        self.assertEqual(EXPECTED_NAMESPACE, namespace["metadata"]["name"])
        self.assertEqual("Namespace", namespace["kind"])

    def test_namespace_and_rbac_bootstrap_are_separate_from_workloads(self):
        self.assertEqual(
            {"namespace.yaml"},
            {
                path.name
                for path in NAMESPACE_DIR.glob("*.yaml")
                if path.name != "kustomization.yaml"
            },
        )
        bootstrap_kinds = {document["kind"] for _, document in self.bootstrap_documents}
        self.assertEqual(
            {"ClusterRole", "ClusterRoleBinding", "Role", "RoleBinding", "ServiceAccount"},
            bootstrap_kinds,
        )
        self.assertFalse(
            {"Deployment", "Service", "ConfigMap", "Secret"} & bootstrap_kinds
        )
        self.assertFalse(
            {"Namespace", "ClusterRole", "ClusterRoleBinding", "Role", "RoleBinding"}
            & {document["kind"] for _, document in self.documents}
        )

    def test_stack_is_not_publicly_exposed(self):
        for service in self.by_kind.get("Service", []):
            self.assertNotIn(
                service["spec"].get("type", "ClusterIP"),
                {"NodePort", "LoadBalancer"},
            )
        self.assertEqual([], self.by_kind.get("Ingress", []))

    def test_kube_state_metrics_permissions_are_read_only(self):
        roles = self.by_kind.get("ClusterRole", [])
        self.assertTrue(roles, "kube-state-metrics needs an explicit read-only role")
        for role in roles:
            for rule in role["rules"]:
                self.assertTrue(set(rule["verbs"]).issubset(READ_ONLY_VERBS))
                self.assertTrue(rule.get("resources"))
                self.assertNotIn("secrets", rule["resources"])
        deployer_role = next(
            role
            for role in self.by_kind.get("Role", [])
            if role["metadata"]["name"] == "monitoring-dev-deployer"
        )
        self.assertIn(
            {
                "apiGroups": [""],
                "resources": ["pods/portforward"],
                "verbs": ["create"],
            },
            deployer_role["rules"],
        )
        self.assertTrue(
            all("secrets" not in rule["resources"] for rule in deployer_role["rules"])
        )
        self.assertFalse(
            any(
                rule["apiGroups"] == ["rbac.authorization.k8s.io"]
                for rule in deployer_role["rules"]
            )
        )

    def test_kube_state_metrics_only_enables_authorized_resources(self):
        deployment = next(
            item
            for item in self.by_kind.get("Deployment", [])
            if item["metadata"]["name"] == "kube-state-metrics"
        )
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        resources_arg = next(
            arg for arg in container["args"] if arg.startswith("--resources=")
        )
        enabled_resources = set(resources_arg.split("=", 1)[1].split(","))
        granted_resources = {
            resource
            for role in self.by_kind.get("ClusterRole", [])
            for rule in role["rules"]
            for resource in rule["resources"]
        }
        self.assertEqual(enabled_resources, granted_resources)

    def test_images_are_version_pinned_and_resources_are_bounded(self):
        for deployment in self.by_kind.get("Deployment", []):
            for container in deployment["spec"]["template"]["spec"]["containers"]:
                image = container["image"]
                self.assertNotIn(":latest", image)
                self.assertIn(":", image.rsplit("/", 1)[-1])
                self.assertIn("requests", container["resources"])
                self.assertIn("limits", container["resources"])
                self.assertIn(
                    "ephemeral-storage", container["resources"]["requests"]
                )
                self.assertIn("ephemeral-storage", container["resources"]["limits"])

    def test_prometheus_storage_and_retention_are_capped(self):
        deployment = next(
            item
            for item in self.by_kind.get("Deployment", [])
            if item["metadata"]["name"] == "prometheus"
        )
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertIn("--storage.tsdb.retention.time=7d", container["args"])
        self.assertIn("--storage.tsdb.retention.size=750MB", container["args"])
        data_volume = next(
            volume
            for volume in deployment["spec"]["template"]["spec"]["volumes"]
            if volume["name"] == "data"
        )
        self.assertEqual("1Gi", data_volume["emptyDir"]["sizeLimit"])

    def test_dev_profile_avoids_unvalidated_host_access_and_persistence(self):
        workload_documents = [
            document
            for document in self.objects
            if document["kind"] in {"Deployment", "DaemonSet", "StatefulSet"}
        ]
        for workload in workload_documents:
            pod_spec = workload["spec"]["template"]["spec"]
            for volume in pod_spec.get("volumes", []):
                self.assertNotIn("hostPath", volume)
                self.assertNotIn("persistentVolumeClaim", volume)
        container_names = {
            container["name"]
            for workload in workload_documents
            for container in workload["spec"]["template"]["spec"]["containers"]
        }
        self.assertNotIn("node-exporter", container_names)

    def test_no_secret_material_is_committed(self):
        self.assertEqual([], self.by_kind.get("Secret", []))
        grafana = next(
            container
            for deployment in self.by_kind.get("Deployment", [])
            if deployment["metadata"]["name"] == "grafana"
            for container in deployment["spec"]["template"]["spec"]["containers"]
        )
        admin_password = next(
            env for env in grafana["env"] if env["name"] == "GF_SECURITY_ADMIN_PASSWORD"
        )
        self.assertIn("secretKeyRef", admin_password["valueFrom"])

    def test_grafana_disables_anonymous_access_and_signup(self):
        grafana = next(
            container
            for deployment in self.by_kind.get("Deployment", [])
            if deployment["metadata"]["name"] == "grafana"
            for container in deployment["spec"]["template"]["spec"]["containers"]
        )
        environment = {entry["name"]: entry for entry in grafana["env"]}
        self.assertEqual("false", environment["GF_AUTH_ANONYMOUS_ENABLED"]["value"])
        self.assertEqual("false", environment["GF_USERS_ALLOW_SIGN_UP"]["value"])

    def test_inventory_script_is_read_only(self):
        script_path = ROOT / "scripts" / "inventory.sh"
        script = script_path.read_text(encoding="utf-8")
        for forbidden in (
            "kubectl apply",
            "kubectl create",
            "kubectl delete",
            "kubectl patch",
            "kubectl edit",
            "kubectl get secrets",
            "kubectl get secret",
        ):
            self.assertNotIn(forbidden, script)
        self.assertIn("kubectl get nodes -o wide", script)
        self.assertIn("kubectl get storageclass -o wide", script)

    def test_rbac_check_script_only_performs_authorization_queries(self):
        script_path = ROOT / "scripts" / "check-rbac.sh"
        script = script_path.read_text(encoding="utf-8")
        for forbidden in (
            "kubectl apply",
            "kubectl create",
            "kubectl delete",
            "kubectl patch",
            "kubectl edit",
        ):
            self.assertNotIn(forbidden, script)
        self.assertIn("kubectl auth can-i", script)
        self.assertIn("kube-state-metrics", script)
        self.assertIn("prometheus", script)

    def test_readme_documents_staged_dev_deployment_and_rollback(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for command in (
            "kubectl apply --dry-run=server -k k8s/dev/namespace",
            "kubectl apply --dry-run=server -k k8s/dev/bootstrap",
            "kubectl apply --dry-run=server -k k8s/dev",
            "kubectl apply -k k8s/dev",
            "kubectl -n monitoring-dev rollout status",
            "kubectl delete -k k8s/dev/namespace",
        ):
            self.assertIn(command, readme)
        self.assertIn("explicit approval", readme.lower())
        self.assertIn("NetworkPolicy", readme)
        self.assertIn("monitoring-dev-deployer", readme)
        self.assertIn("kubectl create token monitoring-dev-deployer", readme)
        self.assertIn("monitoring-dev.kubeconfig", readme)
        self.assertIn("--duration=1h", readme)
        self.assertIn("certificate/key is not copied", readme)
        self.assertIn("kubectl auth can-i create pods/portforward", readme)
        self.assertIn("sudo -v", readme)
        self.assertIn("do not run the whole procedure or a", readme)
        self.assertIn("sudo kubectl apply -k k8s/dev/bootstrap", readme)
        self.assertIn("do not generate a new kubeconfig", readme)

    def test_prometheus_scrapes_only_in_cluster_service_targets(self):
        config_path = STACK_DIR / "prometheus.yml"
        with config_path.open(encoding="utf-8") as prometheus_config:
            config = yaml.safe_load(prometheus_config)
        targets = [
            target
            for job in config["scrape_configs"]
            for static_config in job.get("static_configs", [])
            for target in static_config["targets"]
        ]
        self.assertTrue(targets)
        self.assertTrue(
            all(
                target == "prometheus.monitoring-dev.svc:9090"
                or target == "kube-state-metrics.monitoring-dev.svc:8080"
                for target in targets
            )
        )
        self.assertEqual(
            {"prometheus", "kube-state-metrics"},
            {job["job_name"] for job in config["scrape_configs"]},
        )
        self.assertEqual(["/etc/prometheus/prometheus-rules.yml"], config["rule_files"])

    def test_prometheus_rule_tests_cover_each_alert(self):
        test_path = STACK_DIR / "prometheus-rules-test.yml"
        with test_path.open(encoding="utf-8") as test_file:
            rule_tests = yaml.safe_load(test_file)
        alert_tests = [
            alert
            for test in rule_tests["tests"]
            for alert in test.get("alert_rule_test", [])
        ]
        self.assertEqual(
            {
                "MonitoringTargetDown",
                "KubernetesDeploymentUnavailable",
                "KubernetesNodeNotReady",
                "KubernetesNodeMetricsMissing",
            },
            {alert["alertname"] for alert in alert_tests},
        )

    def test_prometheus_config_is_generated_and_referenced_by_kustomize(self):
        generators = self.kustomization.get("configMapGenerator", [])
        generated_files = {
            generator["name"]: generator["files"] for generator in generators
        }
        self.assertEqual(
            {
                "prometheus-config": ["prometheus.yml", "prometheus-rules.yml"],
                "grafana-dashboards": ["dashboards/cluster-overview.json"],
            },
            generated_files,
        )
        volume_refs = [
            volume["configMap"]["name"]
            for deployment in self.by_kind.get("Deployment", [])
            for volume in deployment["spec"]["template"]["spec"].get("volumes", [])
            if "configMap" in volume
        ]
        self.assertIn("prometheus-config", volume_refs)
        self.assertIn("grafana-dashboards", volume_refs)

    def test_dashboard_contains_queries_for_metrics_that_are_scraped(self):
        dashboard_path = STACK_DIR / "dashboards" / "cluster-overview.json"
        with dashboard_path.open(encoding="utf-8") as dashboard_file:
            dashboard = json.load(dashboard_file)
        expressions = {
            target["expr"]
            for panel in dashboard["panels"]
            for target in panel.get("targets", [])
        }
        self.assertIn('up{job="kube-state-metrics"}', expressions)
        self.assertIn("sum by (phase) (kube_pod_status_phase)", expressions)
        self.assertIn(
            "sum by (namespace) (kube_deployment_status_replicas_available)",
            expressions,
        )
        self.assertIn(
            'kube_node_status_condition{condition="Ready",status="true"}',
            expressions,
        )
        self.assertIn(
            'sum(kube_pod_container_resource_requests{resource="cpu"})',
            expressions,
        )
        self.assertIn(
            'sum(kube_node_status_allocatable{resource="cpu"})',
            expressions,
        )
        self.assertIn(
            'sum(kube_pod_container_resource_requests{resource="memory"})',
            expressions,
        )
        self.assertIn(
            'sum(kube_node_status_allocatable{resource="memory"})',
            expressions,
        )

    def test_deployments_use_restricted_pod_security_defaults(self):
        for deployment in self.by_kind.get("Deployment", []):
            pod_spec = deployment["spec"]["template"]["spec"]
            self.assertFalse(pod_spec.get("hostNetwork", False))
            self.assertFalse(pod_spec.get("hostPID", False))
            self.assertTrue(pod_spec["securityContext"]["runAsNonRoot"])
            for container in pod_spec["containers"]:
                security = container.get("securityContext", {})
                self.assertTrue(security.get("allowPrivilegeEscalation") is False)
                self.assertTrue(security.get("readOnlyRootFilesystem"))

    def test_prometheus_has_no_kubernetes_api_permissions(self):
        bindings = self.by_kind.get("ClusterRoleBinding", []) + self.by_kind.get(
            "RoleBinding", []
        )
        for binding in bindings:
            subjects = binding.get("subjects", [])
            self.assertNotIn(
                "prometheus",
                [subject.get("name") for subject in subjects],
            )

    def test_only_kube_state_metrics_uses_a_cluster_wide_read_role(self):
        bindings = self.by_kind.get("ClusterRoleBinding", [])
        self.assertEqual(1, len(bindings))
        binding = bindings[0]
        self.assertEqual("kube-state-metrics", binding["subjects"][0]["name"])
        self.assertEqual(EXPECTED_NAMESPACE, binding["subjects"][0]["namespace"])

    def test_services_are_cluster_internal(self):
        for service in self.by_kind.get("Service", []):
            self.assertEqual("ClusterIP", service["spec"].get("type", "ClusterIP"))


if __name__ == "__main__":
    unittest.main()
