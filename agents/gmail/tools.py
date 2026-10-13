"""Tools for the gmail agent.

Each tool has two parts:
  - a schema in OpenAI function-calling format (listed in TOOLS)
  - a Python implementation (registered in HANDLERS, keyed by the same name)

Signs in with your Google account (CONNECTS, see core/oauth.py): press Connect
on the Connections page, after turning on the Gmail API in your Google Cloud
project. The access token arrives in GOOGLE_ACCESS_TOKEN. Talks to the Gmail
REST API (https://developers.google.com/gmail/api/reference/rest).
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

CONNECTS = {
    "google": {"scopes": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
    ]},
}

API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_BODY = 6000  # characters of a message's text returned to the model


def _call(method, path, params=None, body=None):
    url = API + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + os.environ["GOOGLE_ACCESS_TOKEN"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read())["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = e.reason
        hint = " Turn on the Gmail API in your Google Cloud project." if e.code == 403 else ""
        raise ValueError(f"Gmail said {e.code}: {message}.{hint}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ValueError(f"Couldn't reach Gmail: {getattr(e, 'reason', e)}")


def _headers(message):
    return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}


def _summary(message):
    h = _headers(message)
    return {"id": message["id"], "thread": message.get("threadId"), "from": h.get("from", ""), "to": h.get("to", ""),
            "subject": h.get("subject", "(no subject)"), "date": h.get("date", ""),
            "unread": "UNREAD" in message.get("labelIds", []), "snippet": message.get("snippet", "")}


def _text(part):
    """The plain text of a message part, falling back to its HTML with the tags crudely removed."""
    found = {"text/plain": [], "text/html": []}

    def walk(p):
        mime, data = p.get("mimeType", ""), p.get("body", {}).get("data")
        if data and mime in found:
            found[mime].append(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace"))
        for child in p.get("parts", []):
            walk(child)
    walk(part)
    if found["text/plain"]:
        return "\n".join(found["text/plain"])
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", "\n".join(found["text/html"]))).strip()


def search_messages(query="", max_results=10):
    max_results = max(1, min(int(max_results), 25))
    params = {"maxResults": max_results, "q": query or "in:inbox"}
    listing = _call("GET", "/messages", params).get("messages", [])
    messages = []
    for item in listing:
        full = _call("GET", f"/messages/{item['id']}", {"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]})
        messages.append(_summary(full))
    return {"query": params["q"], "count": len(messages), "messages": messages}


def read_message(id):
    message = _call("GET", f"/messages/{urllib.parse.quote(str(id), safe='')}", {"format": "full"})
    body = _text(message.get("payload", {}))
    return {**_summary(message), "body": body[:MAX_BODY], "truncated": len(body) > MAX_BODY,
            "attachments": [p["filename"] for p in _parts(message.get("payload", {})) if p.get("filename")]}


def _parts(part):
    yield part
    for child in part.get("parts", []):
        yield from _parts(child)


def _raw(to, subject, body, reply_to_id=None):
    if not to or "@" not in to:
        raise ValueError("Give the recipient's email address.")
    mail = EmailMessage()
    mail["To"], mail["Subject"] = to, subject or "(no subject)"
    mail.set_content(body or "")
    message = {"raw": base64.urlsafe_b64encode(mail.as_bytes()).decode()}
    if reply_to_id:
        original = _call("GET", f"/messages/{urllib.parse.quote(str(reply_to_id), safe='')}", {"format": "metadata", "metadataHeaders": ["Message-ID"]})
        message["threadId"] = original.get("threadId")
        wanted = _headers(original).get("message-id")
        if wanted:
            del mail["In-Reply-To"], mail["References"]
            mail["In-Reply-To"] = mail["References"] = wanted
            message["raw"] = base64.urlsafe_b64encode(mail.as_bytes()).decode()
    return message


def create_draft(to, subject, body, reply_to_id=None):
    draft = _call("POST", "/drafts", body={"message": _raw(to, subject, body, reply_to_id)})
    return {"status": "draft saved", "draft_id": draft.get("id"), "to": to, "subject": subject,
            "note": "It's in the Drafts folder in Gmail. Nothing has been sent."}


def send_message(to, subject, body, reply_to_id=None):
    sent = _call("POST", "/messages/send", body=_raw(to, subject, body, reply_to_id))
    return {"status": "sent", "id": sent.get("id"), "to": to, "subject": subject}


def _tool(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}}


_MAIL = {"to": {"type": "string", "description": "Recipient's email address"},
         "subject": {"type": "string"}, "body": {"type": "string", "description": "Plain text of the email"},
         "reply_to_id": {"type": "string", "description": "id of the message this answers, to keep it in its thread"}}

TOOLS = [
    _tool("search_messages", "Search the mailbox with Gmail's search words (from:, subject:, is:unread, newer_than:7d…). "
          "Empty query lists the latest in the inbox.",
          {"query": {"type": "string"}, "max_results": {"type": "integer", "description": "1 to 25, default 10"}}, []),
    _tool("read_message", "Read one message in full, by the id from search_messages.",
          {"id": {"type": "string"}}, ["id"]),
    _tool("create_draft", "Save an email as a draft in Gmail. Nothing is sent.", _MAIL, ["to", "subject", "body"]),
    _tool("send_message", "Send an email now. Only when the user clearly said to send it.", _MAIL, ["to", "subject", "body"]),
]
HANDLERS = {"search_messages": search_messages, "read_message": read_message,
            "create_draft": create_draft, "send_message": send_message}

VIEWS = {
    "search_messages": {"type": "table", "title": "Messages", "rows": "messages",
                        "columns": [{"field": "from", "label": "From"}, {"field": "subject", "label": "Subject"},
                                    {"field": "date", "label": "Date", "format": "datetime"}]},
}
