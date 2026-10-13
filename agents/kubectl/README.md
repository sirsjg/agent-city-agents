# kubectl — Kubectl

Runs the kubectl CLI on this machine against the clusters in your kubeconfig: list pods and any resource, describe, read logs and events, check usage, scale, restart and undo rollouts, apply manifests, delete, exec into pods, switch contexts, and reach the rest of the CLI (cordon, drain, label, create…). Needs `kubectl` installed and a working kubeconfig. No API key. Commands run without a shell. Interactive and never-ending commands (`edit`, `port-forward`, `proxy`, `-it`, `--watch`, `logs -f`) are refused, and so is printing raw kubeconfig credentials. It asks before deleting or changing anything. `top` needs metrics-server in the cluster.

| Tool | Does |
|---|---|
| `current_context()` | Current context, namespace, and all contexts |
| `use_context(context)` | `kubectl config use-context` |
| `list_pods(namespace, all_namespaces, selector)` | Pods with status, ready, restarts, node |
| `get_resources(kind, name, namespace, all_namespaces, selector)` | `kubectl get <kind> -o wide` |
| `describe(kind, name, namespace)` | `kubectl describe` |
| `pod_logs(pod, namespace, container, lines, previous)` | Last lines of a pod's logs |
| `events(namespace, all_namespaces)` | Latest 40 events, newest last |
| `top(kind, namespace, all_namespaces)` | CPU and memory of pods or nodes, drawn as bar charts under the reply |
| `scale(kind, name, replicas, namespace)` | `kubectl scale` |
| `rollout(action, name, kind, namespace)` | status, restart, undo, history, pause, resume |
| `delete(kind, name, namespace)` | `kubectl delete` |
| `apply_manifest(manifest, namespace)` | `kubectl apply -f -` |
| `exec_in_pod(pod, command, namespace, container)` | Non-interactive `kubectl exec` |
| `run_kubectl(args)` | Any other kubectl command, like `get nodes` or `drain node-1` |

```bash
python3 chat.py kubectl                 # from the project root
python3 chat.py kubectl "Which pods are crashing?" # one-shot
```
