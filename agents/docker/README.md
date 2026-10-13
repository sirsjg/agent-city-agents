# docker — Docker

Runs the Docker CLI on this machine for you: list, start, stop and remove containers, read logs, pull and run images, exec into containers, check usage, and reach the rest of the CLI (networks, volumes, compose, build, prune). Needs Docker installed with the daemon running (Docker Desktop). No API key. Commands run without a shell. Interactive and never-ending commands (`-it`, `logs -f`, `events`) are refused, and so are `login`/`logout`.

| Tool | Does |
|---|---|
| `list_containers(all)` | `docker ps` (`-a` when all is true) |
| `list_images()` | `docker images` |
| `container_logs(container, lines)` | Last lines of a container's logs |
| `container_action(container, action)` | start, stop, restart, pause, unpause, kill or rm |
| `pull_image(image)` | `docker pull` |
| `run_container(image, name, ports, env, command)` | `docker run -d` |
| `exec_in_container(container, command)` | Non-interactive `docker exec` |
| `container_stats()` | One-shot CPU, memory and network use |
| `inspect(target)` | `docker inspect` |
| `disk_usage()` | `docker system df` |
| `run_docker(args)` | Any other docker command, like `network ls` or `compose ps` |

```bash
python3 chat.py docker                 # from the project root
python3 chat.py docker "What containers are running?" # one-shot
```
