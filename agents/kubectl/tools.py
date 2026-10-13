"""Tools for the kubectl agent.

Runs the real `kubectl` CLI on this machine against whatever clusters your
kubeconfig points at. Commands are run without a shell, so nothing the user
types can chain into other programs.
"""

import json
import shlex
import shutil
import subprocess

TIMEOUT = 120
MAX_OUTPUT = 6000


def _kubectl(args, timeout=TIMEOUT, stdin=None):
    exe = shutil.which("kubectl")
    if not exe:
        raise RuntimeError("The kubectl CLI isn't installed or isn't on PATH. Install it first "
                           "(https://kubernetes.io/docs/tasks/tools/).")
    try:
        p = subprocess.run([exe] + list(args), capture_output=True, text=True, timeout=timeout, input=stdin)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"`kubectl {' '.join(args)}` took longer than {timeout}s and was stopped.")
    out = p.stdout.strip()
    err = p.stderr.strip()
    if p.returncode != 0:
        msg = err or out or f"exit code {p.returncode}"
        if "connection refused" in msg or "Unable to connect to the server" in msg:
            msg = "Can't reach the cluster. Check the cluster is running and the current context is right.\n" + msg
        raise RuntimeError(msg[:MAX_OUTPUT])
    return out, err


def _clip(text):
    if len(text) > MAX_OUTPUT:
        return text[:MAX_OUTPUT] + "\n...(output cut)"
    return text


def _both(out, err):
    return _clip("\n".join(x for x in (out, err) if x)) or "(no output)"


def _name(value, what="name"):
    if not value or not str(value).strip():
        raise ValueError(f"Give a {what}.")
    value = str(value).strip()
    if value.startswith("-"):
        raise ValueError(f"'{value}' looks like an option, not a {what}.")
    return value


def _ns(namespace="", all_namespaces=False):
    if all_namespaces:
        return ["-A"]
    if namespace:
        return ["-n", _name(namespace, "namespace")]
    return []


def current_context():
    ctx, _ = _kubectl(["config", "current-context"])
    ns, _ = _kubectl(["config", "view", "--minify", "-o", "jsonpath={..namespace}"])
    out, _ = _kubectl(["config", "get-contexts", "-o", "name"])
    return {"context": ctx, "namespace": ns or "default", "all_contexts": out.splitlines()}


def use_context(context):
    context = _name(context, "context name")
    _kubectl(["config", "use-context", context])
    return {"context": context, "done": True}


def list_pods(namespace="", all_namespaces=False, selector=""):
    args = ["get", "pods", "-o", "json"] + _ns(namespace, all_namespaces)
    if selector:
        args += ["-l", selector]
    out, _ = _kubectl(args)
    rows = []
    for p in json.loads(out).get("items", []):
        statuses = p.get("status", {}).get("containerStatuses", []) or []
        reason = p.get("status", {}).get("phase")
        for s in statuses:
            waiting = s.get("state", {}).get("waiting")
            if waiting:
                reason = waiting.get("reason", reason)
        rows.append({
            "name": p["metadata"]["name"],
            "namespace": p["metadata"].get("namespace"),
            "status": reason,
            "ready": f"{sum(1 for s in statuses if s.get('ready'))}/{len(statuses)}",
            "restarts": sum(s.get("restartCount", 0) for s in statuses),
            "node": p.get("spec", {}).get("nodeName"),
        })
    return {"count": len(rows), "pods": rows}


def get_resources(kind, name="", namespace="", all_namespaces=False, selector=""):
    kind = _name(kind, "resource kind, like 'deployments' or 'svc'")
    args = ["get", kind] + ([_name(name)] if name else []) + _ns(namespace, all_namespaces) + ["-o", "wide"]
    if selector:
        args += ["-l", selector]
    out, err = _kubectl(args)
    return {"command": "kubectl " + " ".join(args), "output": _both(out, err)}


def describe(kind, name, namespace=""):
    kind = _name(kind, "resource kind")
    name = _name(name)
    out, _ = _kubectl(["describe", kind, name] + _ns(namespace))
    return {"kind": kind, "name": name, "describe": _clip(out)}


def pod_logs(pod, namespace="", container="", lines=50, previous=False):
    pod = _name(pod, "pod name")
    lines = max(1, min(int(lines), 500))
    args = ["logs", pod, "--tail", str(lines)] + _ns(namespace)
    if container:
        args += ["-c", _name(container, "container name")]
    if previous:
        args.append("--previous")
    out, err = _kubectl(args)
    return {"pod": pod, "logs": _both(out, err)}


def events(namespace="", all_namespaces=False):
    out, _ = _kubectl(["get", "events", "--sort-by=.lastTimestamp"] + _ns(namespace, all_namespaces))
    lines = out.splitlines()
    if len(lines) > 41:
        lines = lines[:1] + lines[-40:]
    return {"events": _clip("\n".join(lines)) or "(no events)"}


def top(kind="pods", namespace="", all_namespaces=False):
    if kind not in ("pods", "nodes"):
        raise ValueError("kind must be 'pods' or 'nodes'.")
    args = ["top", kind] + ([] if kind == "nodes" else _ns(namespace, all_namespaces))
    out, _ = _kubectl(args)
    lines = out.splitlines()
    if not lines:
        return {"kind": kind, "count": 0, "rows": []}
    # NAME CPU(cores) CPU(%) MEMORY(bytes) MEMORY(%); older kubectl writes CPU% and MEMORY%
    head = [h.lower().replace("(%)", "%").split("(")[0] for h in lines[0].split()]
    rows = []
    for line in lines[1:]:
        cells = dict(zip(head, line.split()))
        row = {"name": cells.get("name"), "cpu_m": _millicores(cells.get("cpu")),
               "memory_mi": _mebibytes(cells.get("memory"))}
        if "namespace" in cells:
            row["namespace"] = cells["namespace"]
        for key, field in (("cpu%", "cpu_percent"), ("memory%", "memory_percent")):
            if cells.get(key, "").rstrip("%").isdigit():
                row[field] = int(cells[key].rstrip("%"))
        rows.append(row)
    rows.sort(key=lambda r: r["cpu_m"] or 0, reverse=True)  # busiest first, in the charts too
    return {"kind": kind, "count": len(rows), "rows": rows}


def _millicores(value):
    """'250m' or '2' (cores) as millicores."""
    try:
        return int(value[:-1]) if value.endswith("m") else round(float(value) * 1000)
    except (AttributeError, ValueError):
        return None


_BYTES = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024}


def _mebibytes(value):
    """'84Mi', '512Ki' or '2Gi' as MiB."""
    try:
        return round(float(value[:-2]) * _BYTES[value[-2:]], 1)
    except (TypeError, KeyError, ValueError):
        return None


def scale(kind, name, replicas, namespace=""):
    kind = _name(kind, "kind, like 'deployment'")
    name = _name(name)
    replicas = int(replicas)
    if replicas < 0:
        raise ValueError("replicas can't be negative.")
    out, _ = _kubectl(["scale", f"{kind}/{name}", f"--replicas={replicas}"] + _ns(namespace))
    return {"result": out}


def rollout(action, name, kind="deployment", namespace=""):
    allowed = ("status", "restart", "undo", "history", "pause", "resume")
    if action not in allowed:
        raise ValueError(f"action must be one of: {', '.join(allowed)}.")
    args = ["rollout", action, f"{_name(kind, 'kind')}/{_name(name)}"] + _ns(namespace)
    if action == "status":
        args.append("--timeout=60s")
    out, err = _kubectl(args)
    return {"result": _both(out, err)}


def delete(kind, name, namespace=""):
    kind = _name(kind, "resource kind")
    name = _name(name)
    out, _ = _kubectl(["delete", kind, name, "--wait=false"] + _ns(namespace))
    return {"result": out}


def apply_manifest(manifest, namespace=""):
    if not manifest or not manifest.strip():
        raise ValueError("Give the YAML or JSON manifest to apply.")
    out, err = _kubectl(["apply", "-f", "-"] + _ns(namespace), stdin=manifest)
    return {"result": _both(out, err)}


def exec_in_pod(pod, command, namespace="", container=""):
    pod = _name(pod, "pod name")
    if not command or not command.strip():
        raise ValueError("Give a command to run, like 'ls /app'.")
    args = ["exec", pod] + _ns(namespace)
    if container:
        args += ["-c", _name(container, "container name")]
    out, err = _kubectl(args + ["--"] + shlex.split(command))
    return {"pod": pod, "output": _both(out, err)}


_INTERACTIVE = ("-i", "-t", "-it", "-ti", "--stdin", "--tty")


def run_kubectl(args):
    """Any other kubectl command. No shell, no 'kubectl' prefix."""
    parts = shlex.split(args or "")
    if parts and parts[0] == "kubectl":
        parts = parts[1:]
    if not parts:
        raise ValueError("Give the kubectl arguments, like 'get nodes' or 'get ingress -A'.")
    if (
        parts[0] in ("edit", "attach", "port-forward", "proxy")
        or any(p in ("-w", "--watch", "--watch-only") for p in parts)
        or (parts[0] == "logs" and any(p in ("-f", "--follow") for p in parts))
        or (parts[0] in ("exec", "run", "debug") and any(p in _INTERACTIVE or p.startswith("--stdin")
                                                          or p.startswith("--tty") for p in parts))
    ):
        raise ValueError("Interactive or never-ending commands can't run here (edit, attach, port-forward, "
                         "proxy, -it, --watch, logs -f). Use exec_in_pod, pod_logs or apply_manifest instead.")
    if parts[0] == "config" and ("--raw" in parts or "--flatten" in parts):
        raise ValueError("I don't print raw kubeconfig credentials.")
    out, err = _kubectl(parts, timeout=600)
    return {"command": "kubectl " + " ".join(parts), "output": _both(out, err)}


# --- OpenAI function-calling schemas -----------------------------------------


def _fn(name, description, props=None, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []},
        },
    }


_NS = {"type": "string", "description": "Namespace, like 'default'. Leave empty for the current one."}
_ALL = {"type": "boolean", "description": "true to look across all namespaces."}
_SEL = {"type": "string", "description": "Optional label selector, like 'app=web'."}
_KIND = {"type": "string", "description": "Resource kind, like 'deployment', 'pod', 'svc', 'configmap'."}
_NAME = {"type": "string", "description": "Resource name, like 'web'."}
_POD = {"type": "string", "description": "Pod name, like 'web-7d9f8c-abcde'."}
_CONTAINER = {"type": "string", "description": "Optional container name, for pods with more than one."}

TOOLS = [
    _fn("current_context", "Show the current cluster context, its namespace and all contexts available."),
    _fn("use_context", "Switch kubectl to another cluster context.",
        {"context": {"type": "string", "description": "Context name, like 'minikube'."}}, ["context"]),
    _fn("list_pods", "List pods with status, readiness, restarts and node.",
        {"namespace": _NS, "all_namespaces": _ALL, "selector": _SEL}),
    _fn("get_resources", "List or show any resource kind (kubectl get -o wide): deployments, services, nodes, "
        "ingress, configmaps, namespaces, pvc, jobs and so on.",
        {"kind": _KIND, "name": {"type": "string", "description": "Optional name to show just one."},
         "namespace": _NS, "all_namespaces": _ALL, "selector": _SEL},
        ["kind"]),
    _fn("describe", "Show full details and recent events for one resource (kubectl describe).",
        {"kind": _KIND, "name": _NAME, "namespace": _NS}, ["kind", "name"]),
    _fn("pod_logs", "Show the last lines of a pod's logs.",
        {"pod": _POD, "namespace": _NS, "container": _CONTAINER,
         "lines": {"type": "integer", "description": "How many lines from the end. Default 50."},
         "previous": {"type": "boolean", "description": "true for the logs of the last crashed run."}},
        ["pod"]),
    _fn("events", "Show the most recent cluster events, newest last. Good for finding why something fails.",
        {"namespace": _NS, "all_namespaces": _ALL}),
    _fn("top", "Show CPU and memory use of pods or nodes right now (needs metrics-server).",
        {"kind": {"type": "string", "enum": ["pods", "nodes"], "description": "pods or nodes. Default pods."},
         "namespace": _NS, "all_namespaces": _ALL}),
    _fn("scale", "Change the replica count of a deployment or statefulset.",
        {"kind": {"type": "string", "description": "deployment or statefulset."}, "name": _NAME,
         "replicas": {"type": "integer", "description": "New replica count, like 3."}, "namespace": _NS},
        ["kind", "name", "replicas"]),
    _fn("rollout", "Restart, check status, undo, show history, pause or resume a rollout.",
        {"action": {"type": "string", "enum": ["status", "restart", "undo", "history", "pause", "resume"],
                    "description": "What to do."},
         "name": _NAME,
         "kind": {"type": "string", "description": "deployment, statefulset or daemonset. Default deployment."},
         "namespace": _NS},
        ["action", "name"]),
    _fn("delete", "Delete one resource. Only after the user has said yes.",
        {"kind": _KIND, "name": _NAME, "namespace": _NS}, ["kind", "name"]),
    _fn("apply_manifest", "Create or update resources from a YAML or JSON manifest (kubectl apply -f -).",
        {"manifest": {"type": "string", "description": "The full YAML or JSON manifest."}, "namespace": _NS},
        ["manifest"]),
    _fn("exec_in_pod", "Run a non-interactive command inside a pod (kubectl exec).",
        {"pod": _POD, "command": {"type": "string", "description": "Command, like 'ls /app'."},
         "namespace": _NS, "container": _CONTAINER},
        ["pod", "command"]),
    _fn("run_kubectl", "Run any other kubectl command, such as 'cordon node-1', 'drain node-1 "
        "--ignore-daemonsets', 'create namespace dev', 'label pod web env=prod', 'auth can-i list pods', "
        "'api-resources' or 'version'. Pass the arguments without the word kubectl.",
        {"args": {"type": "string", "description": "Arguments, like 'get nodes'."}}, ["args"]),
]

HANDLERS = {
    "current_context": current_context,
    "use_context": use_context,
    "list_pods": list_pods,
    "get_resources": get_resources,
    "describe": describe,
    "pod_logs": pod_logs,
    "events": events,
    "top": top,
    "scale": scale,
    "rollout": rollout,
    "delete": delete,
    "apply_manifest": apply_manifest,
    "exec_in_pod": exec_in_pod,
    "run_kubectl": run_kubectl,
}

# Charts and tables under the reply, drawn from the results. A chart has one unit, so CPU and memory get one each.
VIEWS = {
    "top": [
        {"type": "chart", "title": "CPU by {kind}", "rows": "rows", "x": "name", "y": {"cpu_m": "CPU"},
         "kind": "bar", "unit": "m", "format": "int"},
        {"type": "chart", "title": "Memory by {kind}", "rows": "rows", "x": "name", "y": {"memory_mi": "Memory"},
         "kind": "bar", "unit": " Mi", "format": "int"},
    ],
    "list_pods": {
        "type": "table", "title": "{count} pods", "rows": "pods",
        "columns": [
            {"field": "name", "label": "Pod"},
            {"field": "namespace", "label": "Namespace"},
            {"field": "status", "label": "Status"},
            {"field": "ready", "label": "Ready"},
            {"field": "restarts", "label": "Restarts", "format": "int"},
            {"field": "node", "label": "Node"},
        ],
    },
}
