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
        readme = (ROOT / "docs" / "development-deployment.md").read_text(
            encoding="utf-8"
        )
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
        self.assertIn("access to the ClusterIP Services from other pods in the", readme)
        self.assertIn("This is not an acceptable", readme)
        self.assertIn("cannot reach or compromise the", readme)
        self.assertIn("monitoring-dev-deployer", readme)
        self.assertIn("kubectl create token monitoring-dev-deployer", readme)
        self.assertIn("monitoring-dev.kubeconfig", readme)
        self.assertIn("--duration=1h", readme)
        self.assertIn("does not copy the administrator certificate or key", readme)
        self.assertIn(
            "kubectl auth can-i create pods --subresource=portforward", readme
        )
        self.assertIn("sudo -v", readme)
        self.assertIn("Do not run this runbook or a shell block", readme)
        self.assertIn("sudo kubectl apply -k k8s/dev/bootstrap", readme)
        self.assertIn("do not generate a new", readme)

    def test_grafana_secret_guide_uses_interactive_input_and_never_displays_secret_data(self):
        guide = (ROOT / "docs" / "grafana-admin-secret.md").read_text(encoding="utf-8")
        self.assertIn("read -r -s", guide)
        self.assertIn("--from-env-file=/dev/stdin", guide)
        self.assertIn("kubectl create secret generic grafana-admin-credentials", guide)
        self.assertIn(
            "admin-user=%s\\nadmin-password=%s\\n", guide
        )
        self.assertIn("sudo -n kubectl create secret", guide)
        self.assertIn("get secret grafana-admin-credentials -n monitoring-dev -o name", guide)
        self.assertNotIn("get secret grafana-admin-credentials -n monitoring-dev -o yaml", guide)
        self.assertIn("cannot create or read it", guide)

    def test_readme_links_to_split_guides(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("./docs/development-deployment.md", readme)
        self.assertIn("./docs/grafana-admin-secret.md", readme)
        self.assertIn("./docs/ci-versioning-gitops.md", readme)
        self.assertLessEqual(len(readme.splitlines()), 50)

    def test_ci_validates_pull_requests_without_cluster_credentials(self):
        workflow_path = ROOT / ".github" / "workflows" / "validate.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        workflow_config = yaml.safe_load(workflow)
        self.assertIn("jobs", workflow_config)
        self.assertIn("validate", workflow_config["jobs"])
        self.assertIn("pull_request:", workflow)
        self.assertIn("push:", workflow)
        self.assertIn("branches: [master]", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("actions/checkout@", workflow)
        self.assertIn("run: sh scripts/validate.sh", workflow)
        self.assertNotIn("KUBECONFIG", workflow)
        self.assertNotIn("kubectl apply", workflow)
        self.assertNotIn("secrets.", workflow)

    def test_ci_and_gitops_proposal_describes_release_and_argocd_stages(self):
        proposal = (ROOT / "docs" / "ci-versioning-gitops.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Conventional Commit", proposal)
        self.assertIn("v0.1.0", proposal)
        self.assertIn("latest", proposal)
        self.assertIn("Observe-only Argo CD", proposal)
        self.assertIn("cluster-admin", proposal)

    def test_argocd_dev_project_is_limited_to_repo_namespace_and_resource_kinds(self):
        project_path = ROOT / "gitops" / "argocd" / "dev" / "project.yaml"
        project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
        spec = project["spec"]
        self.assertEqual("AppProject", project["kind"])
        self.assertEqual("loutre-sup-dev", project["metadata"]["name"])
        self.assertEqual("argocd", project["metadata"]["namespace"])
        self.assertEqual(
            ["https://github.com/edizbak/loutre-sup.git"], spec["sourceRepos"]
        )
        self.assertEqual(
            [
                {
                    "server": "https://kubernetes.default.svc",
                    "namespace": "monitoring-dev",
                }
            ],
            spec["destinations"],
        )
        self.assertEqual([], spec["clusterResourceWhitelist"])
        self.assertEqual(
            {
                ("", "ConfigMap"),
                ("", "Service"),
                ("", "ServiceAccount"),
                ("apps", "Deployment"),
            },
            {
                (resource["group"], resource["kind"])
                for resource in spec["namespaceResourceWhitelist"]
            },
        )
        self.assertEqual(
            {"ConfigMap", "Service", "ServiceAccount", "Deployment"},
            {document["kind"] for _, document in self.documents},
        )

    def test_argocd_dev_application_observes_only_the_dev_kustomize_path(self):
        application_path = ROOT / "gitops" / "argocd" / "dev" / "application.yaml"
        application = yaml.safe_load(application_path.read_text(encoding="utf-8"))
        spec = application["spec"]
        self.assertEqual("Application", application["kind"])
        self.assertEqual("argocd", application["metadata"]["namespace"])
        self.assertEqual("loutre-sup-dev", spec["project"])
        self.assertEqual(
            {
                "repoURL": "https://github.com/edizbak/loutre-sup.git",
                "targetRevision": "master",
                "path": "k8s/dev",
            },
            spec["source"],
        )
        self.assertEqual(
            {
                "server": "https://kubernetes.default.svc",
                "namespace": "monitoring-dev",
            },
            spec["destination"],
        )
        self.assertNotIn("syncPolicy", spec)

    def test_argocd_dev_bundle_is_rendered_by_local_validation(self):
        kustomization_path = ROOT / "gitops" / "argocd" / "dev" / "kustomization.yaml"
        kustomization = yaml.safe_load(kustomization_path.read_text(encoding="utf-8"))
        self.assertEqual(["project.yaml", "application.yaml"], kustomization["resources"])
        validator = (ROOT / "scripts" / "validate.sh").read_text(encoding="utf-8")
        self.assertIn("gitops/argocd/dev", validator)
        guide = (ROOT / "docs" / "argocd-dev-observe.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("prevent", guide)
        self.assertIn("manual sync", guide)
        self.assertIn("read-only Kubernetes identity", guide)
        self.assertIn("Applying the AppProject and Application is a separate", guide)
        self.assertIn("until that change is approved", guide)

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
        panels = dashboard["panels"]
        expressions = {
            target["expr"] for panel in panels for target in panel.get("targets", [])
        }
        self.assertEqual(len(panels), len({panel["id"] for panel in panels}))
        active_pods_panel = next(panel for panel in panels if panel["id"] == 2)
        self.assertEqual(16, active_pods_panel["gridPos"]["w"])
        for panel in panels:
            grid = panel["gridPos"]
            self.assertLessEqual(grid["x"] + grid["w"], 24)
        self.assertIn("up", expressions)
        self.assertIn(
            'sum by (namespace, phase) (kube_pod_status_phase{phase=~"Pending|Running",namespace=~"$namespace"})',
            expressions,
        )
        self.assertIn(
            'sum by (namespace) (kube_deployment_status_replicas_available{namespace=~"$namespace"})',
            expressions,
        )
        self.assertIn(
            'sum by (namespace) (kube_deployment_spec_replicas{namespace=~"$namespace"})',
            expressions,
        )
        self.assertIn(
            'sum by (namespace) (kube_pod_status_ready{condition="true",namespace=~"$namespace"})',
            expressions,
        )
        self.assertIn(
            'kube_node_status_condition{condition="Ready",status="true"}',
            expressions,
        )
        self.assertIn(
            'sum(kube_pod_container_resource_requests{resource="cpu",namespace=~"$namespace"})',
            expressions,
        )
        self.assertIn(
            'sum(kube_node_status_allocatable{resource="cpu"})',
            expressions,
        )
        self.assertIn(
            'sum(kube_pod_container_resource_requests{resource="memory",namespace=~"$namespace"})',
            expressions,
        )
        self.assertIn(
            'sum(kube_node_status_allocatable{resource="memory"})',
            expressions,
        )
        namespace_variable = next(
            variable
            for variable in dashboard["templating"]["list"]
            if variable["name"] == "namespace"
        )
        self.assertEqual(
            'label_values(kube_pod_status_phase, namespace)',
            namespace_variable["query"],
        )
        self.assertTrue(namespace_variable["includeAll"])
        self.assertEqual(".*", namespace_variable["allValue"])
        resource_panels = {
            panel["title"]: panel for panel in panels if panel["id"] in {5, 6}
        }
        self.assertEqual(
            {"CPU requests vs allocatable capacity", "Memory requests vs allocatable capacity"},
            set(resource_panels),
        )
        for panel in resource_panels.values():
            self.assertIn("not actual runtime usage", panel["description"])

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
