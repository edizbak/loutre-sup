"""Generic Kubernetes manifest assertions reusable across repository test suites."""


def assert_cluster_internal_services(services):
    for service in services:
        service_type = service.get("spec", {}).get("type", "ClusterIP")
        assert service_type == "ClusterIP", (
            f"{service.get('metadata', {}).get('name', 'Service')} must use ClusterIP"
        )


def assert_restricted_pod_security(workloads):
    for workload in workloads:
        pod_spec = workload["spec"]["template"]["spec"]
        name = workload.get("metadata", {}).get("name", "workload")
        assert not pod_spec.get("hostNetwork", False), f"{name} must not use hostNetwork"
        assert not pod_spec.get("hostPID", False), f"{name} must not use hostPID"
        assert pod_spec["securityContext"]["runAsNonRoot"], (
            f"{name} must run as non-root"
        )
        for container in pod_spec["containers"]:
            container_name = container.get("name", "container")
            security = container.get("securityContext", {})
            assert security.get("allowPrivilegeEscalation") is False, (
                f"{name}/{container_name} must disable privilege escalation"
            )
            assert security.get("readOnlyRootFilesystem"), (
                f"{name}/{container_name} must use a read-only root filesystem"
            )


def assert_pinned_images_and_bounded_resources(workloads):
    for workload in workloads:
        for container in workload["spec"]["template"]["spec"]["containers"]:
            name = f"{workload['metadata']['name']}/{container['name']}"
            image = container["image"]
            assert not image.endswith(":latest"), f"{name} must not use latest"
            assert ":" in image.rsplit("/", 1)[-1], f"{name} must pin an image tag"
            resources = container.get("resources", {})
            assert "requests" in resources, f"{name} must define resource requests"
            assert "limits" in resources, f"{name} must define resource limits"
            assert "ephemeral-storage" in resources["requests"], (
                f"{name} must bound requested ephemeral storage"
            )
            assert "ephemeral-storage" in resources["limits"], (
                f"{name} must limit ephemeral storage"
            )


def assert_no_host_or_persistent_volumes(workloads):
    for workload in workloads:
        for volume in workload["spec"]["template"]["spec"].get("volumes", []):
            name = f"{workload['metadata']['name']}/{volume['name']}"
            assert "hostPath" not in volume, f"{name} must not mount a host path"
            assert "persistentVolumeClaim" not in volume, (
                f"{name} must not use a persistent volume claim"
            )
