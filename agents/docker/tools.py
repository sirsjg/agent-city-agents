"""Tools for the docker agent.

Runs the real `docker` CLI on this machine. Commands are run without a shell,
so nothing the user types can chain into other programs.
"""

import json
import shlex
import shutil
import subprocess

TIMEOUT = 120
MAX_OUTPUT = 6000


def _docker(args, timeout=TIMEOUT):
    exe = shutil.which("docker")
    if not exe:
        raise RuntimeError("The docker CLI isn't installed or isn't on PATH. Install Docker Desktop first.")
    try:
        p = subprocess.run([exe] + list(args), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"`docker {' '.join(args)}` took longer than {timeout}s and was stopped.")
    out = p.stdout.strip()
    err = p.stderr.strip()
    if p.returncode != 0:
        msg = err or out or f"exit code {p.returncode}"
        if "Cannot connect to the Docker daemon" in msg or "docker.sock" in msg:
            msg = "The Docker daemon isn't running. Start Docker Desktop and try again."
        raise RuntimeError(msg[:MAX_OUTPUT])
    return out, err


def _clip(text):
    if len(text) > MAX_OUTPUT:
        return text[:MAX_OUTPUT] + "\n...(output cut)"
    return text


def _json_lines(out):
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _name(value, what="name"):
    if not value or not str(value).strip():
        raise ValueError(f"Give a {what}, like 'web' or 'nginx:latest'.")
    value = str(value).strip()
    if value.startswith("-"):
        raise ValueError(f"'{value}' looks like an option, not a {what}.")
    return value


def list_containers(all=False):
    args = ["ps", "--format", "{{json .}}"]
    if all:
        args.insert(1, "-a")
    out, _ = _docker(args)
    rows = [
        {
            "name": c.get("Names"),
            "image": c.get("Image"),
            "status": c.get("Status"),
            "ports": c.get("Ports"),
            "id": c.get("ID"),
        }
        for c in _json_lines(out)
    ]
    return {"count": len(rows), "containers": rows}


def list_images():
    out, _ = _docker(["images", "--format", "{{json .}}"])
    rows = [
        {
            "repository": i.get("Repository"),
            "tag": i.get("Tag"),
            "size": i.get("Size"),
            "created": i.get("CreatedSince"),
            "id": i.get("ID"),
        }
        for i in _json_lines(out)
    ]
    return {"count": len(rows), "images": rows}


def container_logs(container, lines=50):
    container = _name(container, "container name")
    lines = max(1, min(int(lines), 500))
    out, err = _docker(["logs", "--tail", str(lines), container])
    return {"container": container, "logs": _clip("\n".join(x for x in (out, err) if x)) or "(no output)"}


def container_action(container, action):
    container = _name(container, "container name")
    allowed = ("start", "stop", "restart", "pause", "unpause", "kill", "rm")
    if action not in allowed:
        raise ValueError(f"action must be one of: {', '.join(allowed)}.")
    _docker([action, container])
    return {"container": container, "action": action, "done": True}


def pull_image(image):
    image = _name(image, "image")
    out, _ = _docker(["pull", image], timeout=600)
    return {"image": image, "result": _clip(out.splitlines()[-1] if out else "pulled")}


def run_container(image, name="", ports="", env="", command=""):
    """Start a detached container. ports: '8080:80,9000:9000'. env: 'KEY=value,KEY2=value2'."""
    image = _name(image, "image")
    args = ["run", "-d"]
    if name:
        args += ["--name", _name(name)]
    for p in [x.strip() for x in ports.split(",") if x.strip()]:
        args += ["-p", p]
    for e in [x.strip() for x in env.split(",") if x.strip()]:
        if "=" not in e:
            raise ValueError(f"env entry '{e}' must look like KEY=value.")
        args += ["-e", e]
    args.append(image)
    if command:
        args += shlex.split(command)
    out, _ = _docker(args, timeout=600)
    return {"image": image, "name": name or None, "container_id": out.splitlines()[-1][:12]}


def exec_in_container(container, command):
    container = _name(container, "container name")
    if not command or not command.strip():
        raise ValueError("Give a command to run, like 'ls /app'.")
    out, err = _docker(["exec", container] + shlex.split(command))
    return {"container": container, "output": _clip("\n".join(x for x in (out, err) if x)) or "(no output)"}


def container_stats():
    out, _ = _docker(["stats", "--no-stream", "--format", "{{json .}}"])
    rows = [
        {
            "name": s.get("Name"),
            "cpu": s.get("CPUPerc"),
            "memory": s.get("MemUsage"),
            "memory_percent": s.get("MemPerc"),
            "network": s.get("NetIO"),
        }
        for s in _json_lines(out)
    ]
    return {"count": len(rows), "stats": rows}


def inspect(target):
    target = _name(target, "container, image, volume or network name")
    out, _ = _docker(["inspect", target])
    return {"target": target, "inspect": _clip(out)}


def disk_usage():
    out, _ = _docker(["system", "df"])
    return {"usage": out}


def run_docker(args):
    """Any other docker command, e.g. 'network ls' or 'compose ps'. No shell, no 'docker' prefix."""
    parts = shlex.split(args or "")
    if parts and parts[0] == "docker":
        parts = parts[1:]
    if not parts:
        raise ValueError("Give the docker arguments, like 'network ls' or 'volume ls'.")
    if parts[0] in ("login", "logout"):
        raise ValueError("Sign in to registries yourself in a terminal; I don't handle credentials.")
    if (
        parts[0] in ("attach", "events")
        or (parts[0] == "stats" and "--no-stream" not in parts)
        or (parts[0] == "logs" and any(p in ("-f", "--follow") for p in parts))
        or (parts[0] in ("run", "exec") and any(p in ("-i", "-it", "-ti", "-t", "--interactive", "--tty") for p in parts))
    ):
        raise ValueError("Interactive or never-ending commands can't run here. Use run -d, exec_in_container, "
                         "'logs --tail 50' or 'stats --no-stream' instead.")
    out, err = _docker(parts, timeout=600)
    return {"command": "docker " + " ".join(parts), "output": _clip("\n".join(x for x in (out, err) if x)) or "(no output)"}


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


_CONTAINER = {"type": "string", "description": "Container name or ID, like 'web'."}

TOOLS = [
    _fn("list_containers", "List containers (docker ps). Running only unless all is true.",
        {"all": {"type": "boolean", "description": "true to include stopped containers."}}),
    _fn("list_images", "List local images (docker images)."),
    _fn("container_logs", "Show the last lines of a container's logs.",
        {"container": _CONTAINER,
         "lines": {"type": "integer", "description": "How many lines from the end. Default 50."}},
        ["container"]),
    _fn("container_action", "Start, stop, restart, pause, unpause, kill or remove (rm) one container.",
        {"container": _CONTAINER,
         "action": {"type": "string", "enum": ["start", "stop", "restart", "pause", "unpause", "kill", "rm"],
                    "description": "What to do."}},
        ["container", "action"]),
    _fn("pull_image", "Download an image (docker pull).",
        {"image": {"type": "string", "description": "Image with optional tag, like 'nginx:latest'."}},
        ["image"]),
    _fn("run_container", "Start a new detached container from an image (docker run -d).",
        {"image": {"type": "string", "description": "Image, like 'nginx:latest'."},
         "name": {"type": "string", "description": "Optional container name, like 'web'."},
         "ports": {"type": "string", "description": "Optional port maps, comma separated: '8080:80,9000:9000'."},
         "env": {"type": "string", "description": "Optional env vars, comma separated: 'KEY=value,KEY2=value2'."},
         "command": {"type": "string", "description": "Optional command to run instead of the image default."}},
        ["image"]),
    _fn("exec_in_container", "Run a non-interactive command inside a running container (docker exec).",
        {"container": _CONTAINER, "command": {"type": "string", "description": "Command, like 'ls /app'."}},
        ["container", "command"]),
    _fn("container_stats", "Show CPU, memory and network use of running containers right now."),
    _fn("inspect", "Show the full details of a container, image, volume or network (docker inspect).",
        {"target": {"type": "string", "description": "Name or ID, like 'web'."}}, ["target"]),
    _fn("disk_usage", "Show how much disk space images, containers and volumes use (docker system df)."),
    _fn("run_docker", "Run any other docker command, such as 'network ls', 'volume ls', 'compose ps', "
        "'build -t app .', 'system prune -f', 'version' or 'info'. Pass the arguments without the word docker.",
        {"args": {"type": "string", "description": "Arguments, like 'network ls'."}}, ["args"]),
]

HANDLERS = {
    "list_containers": list_containers,
    "list_images": list_images,
    "container_logs": container_logs,
    "container_action": container_action,
    "pull_image": pull_image,
    "run_container": run_container,
    "exec_in_container": exec_in_container,
    "container_stats": container_stats,
    "inspect": inspect,
    "disk_usage": disk_usage,
    "run_docker": run_docker,
}
