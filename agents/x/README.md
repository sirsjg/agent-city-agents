# x — X

Reads your own X (Twitter) account through X's official API, and posts or replies as you after you approve.
Needs an app of yours at https://console.x.com with OAuth 2.0 turned on: save its Client ID and Secret as
`X_CLIENT_ID` and `X_CLIENT_SECRET`, then press Connect for X on the Connections page. X's API is paid per use,
so each read spends a little of that app's credit.

| Tool | Does |
|---|---|
| `my_mentions(since_hours, limit)` | Recent posts that mention or reply to you |
| `home_timeline(limit)` | The latest posts from the accounts you follow |
| `search_posts(query, limit)` | Posts from the last 7 days, with X's search syntax |
| `read_post(post, replies)` | One post in full, with its replies |
| `user_posts(username, limit)` | Someone's latest posts, and who they are |
| `post(text, reply_to, quote)` | Posts, replies or quotes as you, once you approve (`ASKS`: switch that off on its cog) |

```bash
python3 chat.py x "Anything new on X?"   # from the project root
```
