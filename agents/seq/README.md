# seq — SEQ

Reads application logs from a [Seq](https://datalust.co/seq) server (read-only).
Needs the server's address as `SEQ_URL` and a Seq API key with Read permission
as `SEQ_API_KEY`, both saved on the Connections page.

| Tool | Does |
|---|---|
| `search_logs(text, level, minutes, limit, filter)` | Recent events, newest first, narrowed by text, level, time window or a raw Seq filter |
| `error_summary(minutes, level, limit)` | Most frequent messages at a level (default Error) in a window |
| `log_trend(minutes, level, text)` | Event counts over time in even buckets, drawn as a bar chart with peak stats |

```bash
python3 chat.py seq                 # from the project root
python3 chat.py seq "Any errors in the last hour?" # one-shot
```
