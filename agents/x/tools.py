"""Tools for the X agent: reads your X (Twitter) account, says who's talking to you, and posts for you.

It uses X's official API (v2) as you, signed in with OAuth 2.0 by the Connect button on the Connections page
(CONNECTS below; "x" is a preset in core/oauth.py). That needs an app of yours at console.x.com: its OAuth 2.0
Client ID and Secret are the X_CLIENT_ID and X_CLIENT_SECRET keys, and Connect does the rest. X's API is paid per
use, so every read costs a little of the credit on that app; the tools ask for no more posts than they need.

Posting asks the user first (core/approval.py), unless they switched that off on its cog (ASKS).
What people wrote is treated as what they said, never as instructions.
"""

import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from core.approval import approve

API = "https://api.x.com/2"
SCOPES = ["tweet.read", "users.read", "tweet.write", "offline.access"]
CONNECTS = {"x": {"scopes": SCOPES}}
ASKS = "Ask before each post"
STARTERS = [  # automations it offers to add from its settings (core/workflows.py starter)
    {"name": "Morning X mentions",
     "description": "Weekdays at 8, who mentioned you on X overnight and whether anything needs an answer.",
     "trigger": {"type": "schedule", "cron": "0 8 * * 1-5"},
     "steps": [{"kind": "agent", "name": "Mentions", "agent": "self",
                "prompt": "Who mentioned me on X in the last 16 hours? Say what needs an answer first, then the rest "
                          "in a line or two."}]},
]
TIMEOUT = 20
TEXT_LIMIT = 1200  # characters of one post shown to the model
POST_FIELDS = {"tweet.fields": "created_at,author_id,conversation_id,public_metrics,referenced_tweets,note_tweet",
               "expansions": "author_id", "user.fields": "username,name"}
STATUS_URL = re.compile(r"(?:x|twitter)\.com/(?:[^/]+|i/web)/status(?:es)?/(\d+)", re.I)


class XError(Exception):
    pass


def _fetch(method, url, token, body=None):
    """(status, headers, JSON) for one request to X's API."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "User-Agent": "AgentCity", **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, dict(response.headers), json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            found = json.loads(e.read() or b"{}")
        except ValueError:
            found = {}
        return e.code, dict(e.headers or {}), found
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise XError(f"Couldn't reach X: {getattr(e, 'reason', e)}")


def _problem(status, headers, found):
    """What went wrong, in words, from one of X's error answers."""
    detail = (found.get("detail") or found.get("title") or "") if isinstance(found, dict) else ""
    errors = found.get("errors") if isinstance(found, dict) else None
    if not detail and isinstance(errors, list) and errors and isinstance(errors[0], dict):
        detail = errors[0].get("detail") or errors[0].get("message") or ""
    if status == 401:
        return "X didn't accept the sign-in. Press Connect for X on the Connections page again."
    if status == 402 or "credit" in detail.lower():
        return "Your X app is out of API credit. Add some under Billing at https://console.x.com."
    if status == 429:
        reset = (headers or {}).get("x-rate-limit-reset") or (headers or {}).get("X-Rate-Limit-Reset")
        try:
            wait = max(1, int(int(reset) - datetime.now(timezone.utc).timestamp()) // 60 + 1)
            return f"X says that's too many requests for now; try again in about {wait} minutes."
        except (TypeError, ValueError):
            return "X says that's too many requests for now; try again later."
    if status == 403:
        return f"X refused: {detail or 'this app may not be allowed to do that'}. Check the app's permissions at https://console.x.com."
    return f"X answered with an error ({status}){': ' + detail if detail else ''}."


def when(stamp):
    """A post's time, the way people say it: 14:05, yesterday 18:00, Mon 9:30, 3 Feb."""
    try:
        local = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return ""
    now = datetime.now().astimezone()
    days = (now.date() - local.date()).days
    clock = local.strftime("%H:%M")
    if days == 0:
        return clock
    if days == 1:
        return f"yesterday {clock}"
    if days < 7:
        return local.strftime("%a ") + clock
    return local.strftime("%-d %b %Y" if local.year != now.year else "%-d %b")


def post_id(post):
    """A post's id, from its id or its address (https://x.com/someone/status/123…). XError if it's neither."""
    post = str(post or "").strip()
    found = STATUS_URL.search(post)
    if found:
        return found.group(1)
    if post.isdigit():
        return post
    raise XError("Say which post: its id, or its address like https://x.com/someone/status/123.")


def username_of(name):
    name = str(name or "").strip()
    name = re.sub(r"^(https?://)?(www\.)?(x|twitter)\.com/", "", name, flags=re.I).split("/")[0].split("?")[0].lstrip("@")
    if not re.fullmatch(r"\w{1,15}", name):
        raise XError("Say whose: their @username, like @nasa.")
    return name


def lines(found):
    """The posts in one of X's answers, a line each: [id] time · Name @username: text (likes, replies)."""
    people = {u["id"]: u for u in (found.get("includes") or {}).get("users", [])}
    out = []
    for p in found.get("data") or []:
        who = people.get(p.get("author_id"), {})
        name = f"{who.get('name', '')} @{who['username']}".strip() if who.get("username") else "Someone"
        text = ((p.get("note_tweet") or {}).get("text") or p.get("text") or "").strip()
        if len(text) > TEXT_LIMIT:
            text = text[:TEXT_LIMIT - 1] + "…"
        kinds = {r.get("type") for r in p.get("referenced_tweets") or []}
        tag = " (repost)" if "retweeted" in kinds else " (reply)" if "replied_to" in kinds else " (quote)" if "quoted" in kinds else ""
        m = p.get("public_metrics") or {}
        counts = ", ".join(f"{m[k]} {label}" for k, label in (("like_count", "likes"), ("reply_count", "replies"),
                                                              ("retweet_count", "reposts")) if m.get(k))
        out.append(f"[{p['id']}] {when(p.get('created_at'))} · {name}{tag}: {text}" + (f" ({counts})" if counts else ""))
    return "\n".join(out)


def _tool(name, description, properties=None, required=()):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties or {}, "required": list(required)}}}


LIMIT = {"type": "integer", "description": "At most this many posts (default 10, at most 50)."}
POST = {"type": "string", "description": "The post: its id from another tool, or its address (https://x.com/…/status/…)."}
TOOLS = [
    _tool("my_mentions", "Recent posts that mention you or reply to you, newest first. Start here for 'anything new on "
          "X?' or 'who's talking to me?'.",
          {"since_hours": {"type": "integer", "description": "Only the last this many hours (default 24)."}, "limit": LIMIT}),
    _tool("home_timeline", "The latest posts from the accounts you follow, newest first, like your home feed.",
          {"limit": LIMIT}),
    _tool("search_posts", "Find recent posts (the last 7 days) on X. Takes X's search syntax: words, \"exact phrase\", "
          "from:username, to:username, #hashtag, -word, lang:en.",
          {"query": {"type": "string", "description": "What to look for."}, "limit": LIMIT}, ["query"]),
    _tool("read_post", "One post in full, with the replies to it from the last 7 days.",
          {"post": POST, "replies": {"type": "integer", "description": "At most this many replies (default 10, 0 for none)."}},
          ["post"]),
    _tool("user_posts", "Someone's latest posts (not their reposts or replies), and who they are.",
          {"username": {"type": "string", "description": "Their @username."}, "limit": LIMIT}, ["username"]),
    _tool("post", "Post on X as the user, publicly, or reply to or quote a post. The user may have to approve it first. "
          "Only when the user asked for something to be posted; write it the way they would.",
          {"text": {"type": "string", "description": "The post, exactly as it will appear. 280 characters at most, "
                                                     "unless the user has X Premium."},
           "reply_to": {**POST, "description": "The post to reply to (optional)."},
           "quote": {**POST, "description": "The post to quote (optional)."}}, ["text"]),
]

# --- talking to X ---

_lock = threading.Lock()
_me = None  # (token, {id, username, name}) of the account signed in, asked once per sign-in


def _token():
    token = os.environ.get("X_ACCESS_TOKEN")
    if not token:
        raise XError("X isn't connected. Press Connect for X on the Connections page.")
    return token


def _api(method, path, query=None, body=None):
    """X's answer to one request. XError says what's wrong."""
    url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    status, headers, found = _fetch(method, url, _token(), body)
    if status >= 400 or (isinstance(found, dict) and "data" not in found and found.get("errors")):
        if status < 400 and found.get("errors"):  # X answers 200 with only errors when a post or user isn't there
            raise XError(found["errors"][0].get("detail") or "X couldn't find that.")
        raise XError(_problem(status, headers, found))
    return found


def _whoami():
    global _me
    token = _token()
    with _lock:
        if _me is None or _me[0] != token:
            _me = (token, _api("GET", "/users/me", {"user.fields": "username,name"})["data"])
        return _me[1]


def _posts(path, limit, more=None, least=5):
    """Some posts, as lines. X has a smallest number it lists for each address (least): ask for that, cut to limit."""
    limit = max(1, min(int(limit or 10), 50))
    found = _api("GET", path, {**POST_FIELDS, "max_results": max(limit, least), **(more or {})})
    found["data"] = (found.get("data") or [])[:limit]
    return lines(found)


def _answer(make):
    try:
        return make()
    except XError as e:
        return {"error": str(e)}


# --- the tools ---

def my_mentions(since_hours=24, limit=10):
    def go():
        hours = max(1, min(int(since_hours or 24), 24 * 7))
        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        me = _whoami()
        found = _posts(f"/users/{me['id']}/mentions", limit, {"start_time": start})
        return {"you": f"@{me['username']}", "mentions": found or f"Nobody mentioned you in the last {hours} hours."}
    return _answer(go)


def home_timeline(limit=10):
    def go():
        me = _whoami()
        return {"posts": _posts(f"/users/{me['id']}/timelines/reverse_chronological", limit, least=1)
                or "Nothing in your feed."}
    return _answer(go)


def search_posts(query, limit=10):
    query = str(query or "").strip()
    if not query:
        return {"error": "Say what to look for."}
    return _answer(lambda: {"query": query, "posts": _posts("/tweets/search/recent", limit, {"query": query}, least=10)
                            or "Nothing found in the last 7 days."})


def read_post(post, replies=10):
    def go():
        id = post_id(post)
        found = _api("GET", f"/tweets/{id}", POST_FIELDS)
        found["data"] = [found["data"]]
        out = {"post": lines(found)}
        conversation = found["data"][0].get("conversation_id") or id
        if int(replies or 0) > 0:
            out["replies"] = _posts("/tweets/search/recent", replies, {"query": f"conversation_id:{conversation}"},
                                    least=10) or "No replies in the last 7 days."
        return out
    return _answer(go)


def user_posts(username, limit=10):
    def go():
        name = username_of(username)
        user = _api("GET", f"/users/by/username/{name}",
                    {"user.fields": "name,username,description,public_metrics,verified"})["data"]
        counts = user.get("public_metrics") or {}
        who = f"{user.get('name', '')} @{user['username']}" + (f": {user['description']}" if user.get("description") else "")
        if counts:
            who += f" ({counts.get('followers_count', 0)} followers)"
        posts = _posts(f"/users/{user['id']}/tweets", limit, {"exclude": "retweets,replies"})
        return {"who": who, "posts": posts or "No posts."}
    return _answer(go)


def post(text, reply_to=None, quote=None):
    text = str(text or "").strip()
    if not text:
        return {"error": "Write the post."}
    def go():
        body = {"text": text}
        if reply_to:
            body["reply"] = {"in_reply_to_tweet_id": post_id(reply_to)}
        if quote:
            body["quote_tweet_id"] = post_id(quote)
        quoted = "\n".join(f"> {line}" for line in text.splitlines())
        what = (f"Reply to post {body['reply']['in_reply_to_tweet_id']}" if "reply" in body
                else f"Quote post {body['quote_tweet_id']}" if "quote_tweet_id" in body else "Post publicly")
        approve("Post", f"{what} on **X**:\n\n{quoted}")  # stops here unless the user lets it go
        made = _api("POST", "/tweets", body=body)["data"]
        me = _whoami()
        return {"posted": True, "id": made["id"], "url": f"https://x.com/{me['username']}/status/{made['id']}"}
    return _answer(go)


HANDLERS = {"my_mentions": my_mentions, "home_timeline": home_timeline, "search_posts": search_posts,
            "read_post": read_post, "user_posts": user_posts, "post": post}
