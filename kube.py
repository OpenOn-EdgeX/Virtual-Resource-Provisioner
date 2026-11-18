from pathlib import Path

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError

from lifecycle import Problem


class Kube:
    def __init__(self, path, context):
        if not path or not Path(path).is_file() or not context or not context.strip():
            raise ValueError("Set a kubeconfig file and an explicit context")
        self.client = config.new_client_from_config(config_file=path, context=context, persist_config=False)
        self.apps = client.AppsV1Api(self.client)

    def request(self, fn, **kwargs):
        try:
            result = fn(_request_timeout=(3, 10), **kwargs)
        except ApiException as exc:
            status = exc.status if exc.status in (400, 403, 404, 409, 422, 429) else 502
            raise Problem("Kubernetes request failed", status) from exc
        except HTTPError as exc:
            raise Problem("Kubernetes API unavailable", 503) from exc
        return self.client.sanitize_for_serialization(result)

    def create(self, namespace, body):
        return self.request(self.apps.create_namespaced_deployment, namespace=namespace, body=body)

    def get(self, namespace, name):
        return self.request(self.apps.read_namespaced_deployment, namespace=namespace, name=name)

    def patch(self, namespace, name, body):
        return self.request(self.apps.patch_namespaced_deployment, namespace=namespace, name=name, body=body)

    def delete(self, namespace, name, body):
        return self.request(self.apps.delete_namespaced_deployment, namespace=namespace, name=name, body=body)
