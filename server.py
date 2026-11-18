import hmac
import os

from flask import Flask, request

from lifecycle import Lifecycle, Problem


PREFIX = "/rest/1.0/deployment_management"


def create_app(kube, namespace, token):
    if not isinstance(token, str) or len(token) < 32 or not token.isascii() or not token.isalnum():
        raise ValueError("Set a random alphanumeric PROVISIONER_TOKEN of at least 32 characters")
    service = Lifecycle(kube, namespace)
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    expected = f"Bearer {token}".encode()

    @app.before_request
    def authorize():
        if not hmac.compare_digest(request.headers.get("Authorization", "").encode(), expected):
            return {"error": "Authentication required"}, 401, {"WWW-Authenticate": "Bearer"}

    @app.errorhandler(Problem)
    def error(exc):
        return {"error": str(exc)}, exc.status

    @app.post(f"{PREFIX}/create")
    def create():
        return service.create(request.get_json()), 201

    @app.patch(f"{PREFIX}/resources")
    def resources():
        return service.update(request.get_json())

    @app.patch(f"{PREFIX}/replicas")
    def replicas():
        return service.update(request.get_json(), scaling=True)

    @app.delete(f"{PREFIX}/delete/<namespace>/<name>")
    def delete(namespace, name):
        return service.release(namespace, name)

    return app


if __name__ == "__main__":
    from kube import Kube

    kube = Kube(os.environ["KUBECONFIG"], os.environ["PROVISIONER_CONTEXT"])
    app = create_app(kube, os.environ["PROVISIONER_NAMESPACE"], os.environ["PROVISIONER_TOKEN"])
    app.run(host="127.0.0.1", port=8080, debug=False, use_reloader=False)
