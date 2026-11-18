import re
import time
from decimal import Decimal


GPU = "nvidia.com/gpu"
HOST = "kubernetes.io/hostname"
MANAGER = "app.kubernetes.io/managed-by"
PROJECT = "virtual-resource-provisioner"
STRATEGY = {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1}}
DNS_LABEL = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")
QUANTITY = re.compile(r"(\d+(?:\.\d*)?|\.\d+)([EPTGMK]i|[EPTGMkmun]|[eE][+-]?\d{1,2})?")
SI = {"": 0, "n": -9, "u": -6, "m": -3, "k": 3, "M": 6, "G": 9, "T": 12, "P": 15, "E": 18}


class Problem(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def label(value, field):
    if not isinstance(value, str) or not DNS_LABEL.fullmatch(value):
        raise Problem(f"{field} must be a Kubernetes DNS label")
    return value


def count(value, field):
    if type(value) is not int or not 0 <= value <= 2**31 - 1:
        raise Problem(f"{field} must be a non-negative integer")
    return value


def quantity(value):
    if not isinstance(value, str) or len(value) > 64 or not (m := QUANTITY.fullmatch(value)):
        raise Problem("Invalid resource quantity")
    number, suffix = Decimal(m[1]), m[2] or ""
    if suffix.endswith("i"):
        return number * 1024 ** ("KMGTPE".index(suffix[0]) + 1)
    return number.scaleb(SI[suffix] if suffix in SI else int(suffix[1:]))


def _merge_resource_requirements(
    existing_requests,
    existing_limits,
    cpu_request,
    cpu_limit,
    memory_request,
    memory_limit,
    gpu_provided,
    gpu,
):
    requests = dict(existing_requests)
    limits = dict(existing_limits)
    removed_keys = []

    if cpu_request:
        requests["cpu"] = cpu_request
    if cpu_limit:
        limits["cpu"] = cpu_limit
    if memory_request:
        requests["memory"] = memory_request
    if memory_limit:
        limits["memory"] = memory_limit

    if gpu_provided:
        if gpu > 0:
            gpu_value = str(gpu)
            requests["nvidia.com/gpu"] = gpu_value
            limits["nvidia.com/gpu"] = gpu_value
        else:
            if "nvidia.com/gpu" in requests:
                removed_keys.append("nvidia.com/gpu")
                del requests["nvidia.com/gpu"]
            if "nvidia.com/gpu" in limits:
                if "nvidia.com/gpu" not in removed_keys:
                    removed_keys.append("nvidia.com/gpu")
                del limits["nvidia.com/gpu"]

    return requests, limits, removed_keys


def _prepare_patch_resources(requests, limits, removed_keys):
    patch_requests = dict(requests)
    patch_limits = dict(limits)

    for key in removed_keys:
        patch_requests[key] = None
        patch_limits[key] = None

    return patch_requests, patch_limits


def resource_patch(payload, existing=None):
    current = existing or {}
    fields = {key: payload.get(key) for key in ("cpu_request", "cpu_limit", "memory_request", "memory_limit")}
    for key in fields:
        if key in payload:
            quantity(fields[key])
    requests, limits, removed = _merge_resource_requirements(
        current.get("requests") or {}, current.get("limits") or {}, **fields,
        gpu_provided="gpu" in payload, gpu=count(payload.get("gpu", 0), "gpu"),
    )
    for resource in ("cpu", "memory"):
        if resource in requests and resource in limits and quantity(requests[resource]) > quantity(limits[resource]):
            raise Problem(f"{resource} request must be less than or equal to limit")
    patch_requests, patch_limits = _prepare_patch_resources(requests, limits, removed)
    return {"requests": patch_requests, "limits": patch_limits}


def make_deployment(namespace, name, payload):
    image = payload.get("image")
    if not isinstance(image, str) or not image.strip() or len(image) > 2048 or any(c.isspace() for c in image):
        raise Problem("image is required and cannot contain whitespace")
    container = {"name": label(payload.get("container_name", name), "container_name"), "image": image,
                 "resources": resource_patch(payload), "securityContext": {"allowPrivilegeEscalation": False,
                 "runAsNonRoot": True, "capabilities": {"drop": ["ALL"]}}}
    pod = {"containers": [container], "automountServiceAccountToken": False,
           "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}}}
    if "node_name" in payload:
        node = payload["node_name"]
        if not isinstance(node, str) or len(node) > 253 or not all(DNS_LABEL.fullmatch(part) for part in node.split(".")):
            raise Problem("Invalid node_name")
        pod["nodeSelector"] = {HOST: node}
    labels = {"app": name, MANAGER: PROJECT}
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "namespace": namespace, "labels": labels},
            "spec": {"replicas": count(payload.get("replicas", 1), "replicas"), "selector": {"matchLabels": labels},
                     "strategy": STRATEGY, "template": {"metadata": {"labels": labels}, "spec": pod}}}


def selected(deployment, container_name=None):
    containers = deployment["spec"]["template"]["spec"].get("containers", [])
    if container_name is not None:
        for container in containers:
            if container["name"] == container_name:
                return container
        raise Problem("Container not found", 404)
    if len(containers) != 1:
        raise Problem("container_name is required when deployment has multiple containers")
    return containers[0]


def snapshot(deployment, container=None):
    meta, spec = deployment["metadata"], deployment["spec"]
    template = spec["template"]["spec"]
    target = selected(deployment, container) if container else template["containers"][0]
    resources = target.get("resources") or {}
    return {"name": meta["name"], "namespace": meta["namespace"], "replicas": spec["replicas"],
            "ready_replicas": (deployment.get("status") or {}).get("readyReplicas", 0),
            "container_name": target["name"], "image": target["image"],
            "node_name": template.get("nodeName") or (template.get("nodeSelector") or {}).get(HOST),
            "labels": meta.get("labels") or {}, "selector": spec["selector"].get("matchLabels") or {},
            "resources": {key: resources.get(key) or {} for key in ("requests", "limits")}}


class Lifecycle:
    def __init__(self, kube, namespace):
        self.kube = kube
        self.namespace = label(namespace, "namespace")
        if namespace == "default" or namespace.startswith("kube-"):
            raise Problem("Use a dedicated non-system namespace")

    def target(self, payload):
        if not isinstance(payload, dict):
            raise Problem("Expected a JSON object")
        if payload.get("namespace") != self.namespace:
            raise Problem("Namespace outside configured scope", 403)
        return label(payload.get("name"), "name")

    def managed(self, name):
        obj = self.kube.get(self.namespace, name)
        if (obj["metadata"].get("labels") or {}).get(MANAGER) != PROJECT:
            raise Problem("Deployment is not managed by this provisioner", 403)
        return obj

    def create(self, payload):
        name = self.target(payload)
        return snapshot(self.kube.create(self.namespace, make_deployment(self.namespace, name, payload)))

    def update(self, payload, scaling=False):
        name = self.target(payload)
        obj = self.managed(name)
        container_name = None
        if scaling:
            spec = {"replicas": count(payload.get("replicas"), "replicas")}
        else:
            if not {"cpu_request", "cpu_limit", "memory_request", "memory_limit", "gpu"}.intersection(payload):
                raise Problem("At least one resource field must be provided")
            target = selected(obj, payload.get("container_name"))
            container_name = target["name"]
            container = {"name": container_name, "resources": resource_patch(payload, target.get("resources"))}
            spec = {"strategy": STRATEGY, "template": {"spec": {"containers": [container]}}}
        patch = {"metadata": {"resourceVersion": obj["metadata"]["resourceVersion"]}, "spec": spec}
        return snapshot(self.kube.patch(self.namespace, name, patch), container_name)

    def release(self, namespace, name, timeout=30):
        self.target({"namespace": namespace, "name": name})
        meta = self.managed(name)["metadata"]
        self.kube.delete(namespace, name, {"propagationPolicy": "Foreground", "preconditions": {
            "uid": meta["uid"], "resourceVersion": meta["resourceVersion"]}})
        started = time.monotonic()
        while True:
            try:
                current = self.kube.get(namespace, name)
            except Problem as exc:
                if exc.status != 404:
                    raise
                current = None
            done = current is None or current["metadata"]["uid"] != meta["uid"]
            elapsed = time.monotonic() - started
            if done or elapsed >= timeout:
                return {"name": name, "namespace": namespace, "deleted": done,
                        "elapsed_seconds": round(elapsed, 2)}, 200 if done else 202
            time.sleep(min(1, timeout - elapsed))
