# Deployment lifecycle API

Use Python 3.10+ and a dedicated test namespace. The process requires an explicit kubeconfig file and context. Grant that identity `get`, `create`, `patch`, and `delete` on `apps/deployments` in **only that namespace**. No cluster-wide or Pod permissions are needed. Use trusted kubeconfig files only.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest -v
export KUBECONFIG=/path/to/test-kubeconfig
export PROVISIONER_CONTEXT=your-test-context
export PROVISIONER_NAMESPACE=sandbox
export PROVISIONER_TOKEN="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python server.py
```

The development server listens only on `127.0.0.1:8080`. All endpoints require the same operator bearer token; this is not multi-user authorization. Do not proxy this service to untrusted clients. Apply namespace quotas, admission policy, and image restrictions in Kubernetes. Use a trusted non-root workload image compatible with dropped capabilities. Do not commit credentials or kubeconfig files.

In a shell sharing those environment variables, set `WORKLOAD_IMAGE` to your test image reference. These commands change real Deployments in `sandbox`:

```sh
: "${WORKLOAD_IMAGE:?set a trusted test image}"
api=http://127.0.0.1:8080/rest/1.0/deployment_management
call() {
  printf 'Authorization: Bearer %s\n' "$PROVISIONER_TOKEN" | curl --fail-with-body -sS -H @- -H 'Content-Type: application/json' "$@"
}
call -X POST "$api/create" --data "{\"namespace\":\"sandbox\",\"name\":\"worker\",\"replicas\":1,\"image\":\"$WORKLOAD_IMAGE\",\"cpu_request\":\"100m\",\"memory_limit\":\"256Mi\"}"
call -X PATCH "$api/resources" --data '{"namespace":"sandbox","name":"worker","cpu_limit":"500m","gpu":0}'
call -X PATCH "$api/replicas" --data '{"namespace":"sandbox","name":"worker","replicas":0}'
call -X DELETE "$api/delete/sandbox/worker"
```

- Create: `201` means the Deployment was accepted, not that Pods are ready. Optional `node_name` sets a hostname selector. Optional `gpu` requests whole GPUs using `nvidia.com/gpu`.
- Resource update: unspecified fields are preserved; `gpu: 0` removes the GPU request and limit. A multi-container Deployment requires `container_name` for resource changes. Replica updates accept zero.
- Update/delete are limited to Deployments carrying this provisioner’s management label. Label checks are not a security boundary against other users with write access to the namespace. A concurrent change may return `409`; inspect before retrying.
- Delete uses foreground propagation, never force-deletes Pods, and polls for disappearance of the target UID for 30 seconds (an in-flight request may take longer). `202` means deletion is still pending; inspect with `kubectl get deployment,pods -n sandbox`. A `200` is not a measurement of hardware availability.
- Resource fit, quota, admission, and placement are evaluated by Kubernetes. Offline capacity estimation is separate from the mutation API. No external monitoring integration or GPU runtime is required by the provisioner.
