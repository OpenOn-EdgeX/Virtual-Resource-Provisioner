import copy
import unittest
from unittest.mock import Mock

from lifecycle import GPU, MANAGER, Lifecycle, Problem, make_deployment, resource_patch


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.kube = Mock()
        self.service = Lifecycle(self.kube, "sandbox")
        self.base = {"namespace": "sandbox", "name": "worker"}
        self.obj = make_deployment("sandbox", "worker", {"image": "example.invalid/test:v1"})
        self.obj["metadata"].update({"uid": "uid-1", "resourceVersion": "2"})
        self.kube.get.return_value = self.obj
        self.kube.patch.return_value = self.obj
        self.kube.create.side_effect = lambda ns, body: body

    def test_create(self):
        result = self.service.create({**self.base, "image": "example.invalid/test:v1", "gpu": 1, "node_name": "demo-node"})
        self.assertEqual(result["resources"]["requests"][GPU], "1")
        self.assertEqual(result["node_name"], "demo-node")
        self.assertFalse(self.kube.create.call_args.args[1]["spec"]["template"]["spec"]["automountServiceAccountToken"])

    def test_invalid_and_out_of_scope(self):
        for extra in ({"namespace": "other"}, {"gpu": True}, {"replicas": -1}, {"cpu_request": "broken"},
                      {"cpu_request": "1", "cpu_limit": "800m"}, {"memory_request": "1Gi", "memory_limit": "1G"}):
            with self.subTest(extra=extra), self.assertRaises(Problem):
                self.service.create({**self.base, "image": "example.invalid/test:v1", **extra})
        with self.assertRaises(Problem):
            self.service.create(self.base)
        self.kube.create.assert_not_called()

    def test_resource_patch(self):
        existing = {"requests": {"cpu": "100m", GPU: "1"}, "limits": {GPU: "1", "memory": "1Gi"}}
        patch = resource_patch({"cpu_request": "250m", "gpu": 0}, existing)
        self.assertEqual(patch, {"requests": {"cpu": "250m", GPU: None}, "limits": {GPU: None, "memory": "1Gi"}})
        self.assertEqual(existing["requests"][GPU], "1")
        self.service.update({**self.base, "cpu_request": "250m"})
        self.assertEqual(self.kube.patch.call_args.args[2]["spec"]["template"]["spec"]["containers"][0]["name"], "worker")

    def test_multicontainer(self):
        containers = self.obj["spec"]["template"]["spec"]["containers"]
        containers.append({**copy.deepcopy(containers[0]), "name": "second"})
        with self.assertRaises(Problem):
            self.service.update({**self.base, "cpu_limit": "1"})
        self.service.update({**self.base, "container_name": "second", "cpu_limit": "1"})
        patch = self.kube.patch.call_args.args[2]["spec"]["template"]["spec"]["containers"]
        self.assertEqual([c["name"] for c in patch], ["second"])

    def test_scale_zero_with_precondition(self):
        self.service.update({**self.base, "replicas": 0}, scaling=True)
        self.assertEqual(self.kube.patch.call_args.args[2], {"metadata": {"resourceVersion": "2"}, "spec": {"replicas": 0}})

    def test_only_owned_deployments(self):
        del self.obj["metadata"]["labels"][MANAGER]
        with self.assertRaises(Problem) as denied:
            self.service.release("sandbox", "worker", timeout=0)
        self.assertEqual(denied.exception.status, 403)
        self.kube.delete.assert_not_called()

    def test_release_pending_and_completed(self):
        pending, status = self.service.release("sandbox", "worker", timeout=0)
        self.assertEqual((pending["deleted"], status), (False, 202))
        body = self.kube.delete.call_args.args[2]
        self.assertEqual(body, {"propagationPolicy": "Foreground", "preconditions": {"uid": "uid-1", "resourceVersion": "2"}})
        self.kube.get.side_effect = [self.obj, Problem("not found", 404)]
        done, status = self.service.release("sandbox", "worker", timeout=0)
        self.assertEqual((done["deleted"], status), (True, 200))


class ApiTest(unittest.TestCase):
    def test_authentication_and_http_contract(self):
        from secrets import token_hex
        from server import PREFIX, create_app
        token = token_hex(32)
        gateway = Mock()
        gateway.create.side_effect = lambda ns, body: body
        client = create_app(gateway, "sandbox", token).test_client()
        url = PREFIX + "/create"
        auth = {"Authorization": f"Bearer {token}"}
        payload = {"namespace": "sandbox", "name": "worker", "image": "example.invalid/test:v1"}
        self.assertEqual(client.post(url, json=payload).status_code, 401)
        self.assertEqual(client.post(url, headers=auth, json={**payload, "namespace": "other"}).status_code, 403)
        gateway.create.assert_not_called()
        created = client.post(url, headers=auth, json=payload)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["ready_replicas"], 0)
        obj = make_deployment("sandbox", "worker", payload)
        obj["metadata"].update({"uid": "old", "resourceVersion": "3"})
        gateway.get.return_value = gateway.patch.return_value = obj
        for endpoint, args in (("resources", {"memory_limit": "256Mi"}), ("replicas", {"replicas": 0})):
            response = client.patch(PREFIX + "/" + endpoint, headers=auth, json={**payload, **args})
            self.assertEqual(response.status_code, 200)
        gateway.get.side_effect = [obj, Problem("gone", 404)]
        self.assertEqual(client.delete(PREFIX + "/delete/sandbox/worker", headers=auth).status_code, 200)


class AdapterTest(unittest.TestCase):
    def test_generated_sdk_sends_strategic_patch(self):
        import json
        from unittest.mock import patch
        from kubernetes import client
        from urllib3.response import HTTPResponse
        from kube import Kube
        config = client.Configuration()
        config.host = "https://example.invalid"
        api = client.ApiClient(config)
        self.addCleanup(api.close)
        with patch("kube.config.new_client_from_config", return_value=api):
            kube = Kube(__file__, "test")
        obj = make_deployment("sandbox", "worker", {"image": "example.invalid/test:v1"})
        with patch.object(api.rest_client.pool_manager, "request", return_value=HTTPResponse(
                body=json.dumps(obj).encode(), status=200, headers={"Content-Type": "application/json"})) as transport:
            body = {"metadata": {"resourceVersion": "3"}, "spec": {"replicas": 0}}
            response = kube.patch("sandbox", "worker", body)
        args, kwargs = transport.call_args
        self.assertEqual(args[0], "PATCH")
        self.assertTrue(args[1].endswith("/apis/apps/v1/namespaces/sandbox/deployments/worker"))
        self.assertEqual(json.loads(kwargs["body"]), body)
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/strategic-merge-patch+json")
        self.assertEqual(response["metadata"]["name"], "worker")


if __name__ == "__main__":
    unittest.main()
