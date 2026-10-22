"""Tools for the Telegram agent: reads your Telegram chats, says what's waiting and what's urgent, and replies for you.

It signs in to your own account (not a bot: a bot only sees messages sent to the bot) with Telethon, Telegram's
official user API, the way Telegram's own apps do; Telethon comes with Agent City. That needs an app of yours
registered at my.telegram.org: its api_id and api_hash are the TELEGRAM_API_ID and TELEGRAM_API_HASH keys. Then its
row on the Connections page signs in (SIGNIN, core/signin.py): your phone number, the code Telegram sends to your
other devices, and your two-step password if you have one. The session is kept in Agent City's memory folder, as
telegram.session, so it stays signed in; Disconnect ends it at Telegram too.

While it's on, it listens (LISTEN, core/listening.py): each new message is heard as it arrives. Those from a person
to you, or that mention you in a group (not in channels or muted chats), are judged for urgency, and anything urgent
is announced in Activity and on paired phones, unless that's switched off on its cog (ALERTS). Automations that
start when a message arrives hear them all.

Reading never marks anything as read. Sending asks the user first (core/approval.py), unless they switched that off
on its cog (ASKS). Telethon is asynchronous, so its client lives on an event loop of its own, in a thread; everything
here is called from other threads and waits for it.
"""

import asyncio
import concurrent.futures
import os
import threading
import time
from datetime import datetime, timezone

from core import paths
from core.approval import NotApproved, approve
from core.texting import reply_id, styled, unescape

KEYS = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH")
NEEDS = {
    "TELEGRAM_API_ID": "Your Telegram app's api_id: sign in at https://my.telegram.org, open API development tools and "
                       "create an app (any name)",
    "TELEGRAM_API_HASH": "Your Telegram app's api_hash, from the same page as its api_id",
}
ASKS = "Ask before sending each message"
ALERTS = "Tell me when something urgent arrives"
STARTERS = [  # automations it offers to add from its settings (core/workflows.py starter)
    {"name": "Reply ideas for urgent Telegram messages",
     "description": "When something urgent arrives on Telegram, it suggests a short reply in your voice. It never sends one.",
     "trigger": {"type": "message", "from": "telegram", "only": "urgent"},
     "steps": [{"kind": "agent", "name": "Suggest replies", "agent": "telegram",
                "prompt": "These urgent messages just arrived:\n\n{{trigger}}\n\nFor each, say who it's from and what "
                          "they need, then suggest a short reply in my voice. Don't send anything."}]},
    {"name": "Telegram catch-up",
     "description": "Every evening at 6, who's waiting on you on Telegram, and what they want.",
     "trigger": {"type": "schedule", "cron": "0 18 * * *"},
     "steps": [{"kind": "agent", "name": "Catch up", "agent": "telegram",
                "prompt": "What's unread on Telegram? Say who's waiting on me and what they want, most important "
                          "first, in a line each. Skip chit-chat."}]},
]
TIMEOUT = 60  # seconds to wait for Telegram
TEXT_LIMIT = 1200  # characters of one message shown to the model
UNSENT = {"skipped": "The user skipped it, so it wasn't sent.", "unanswered": "Nobody answered, so it wasn't sent.",
          "no_window": "I can only read from here: sending needs Agent City's window, to ask you first."}


class TelegramError(Exception):
    pass


def _client(api_id, api_hash, session):
    from telethon import TelegramClient
    return TelegramClient(str(session), int(api_id), api_hash, device_model="Agent City", app_version="Agent City")


def _password_needed(e):
    return type(e).__name__ == "SessionPasswordNeededError"


def _friendly(e):
    """What went wrong, in words, from one of Telethon's errors."""
    name = type(e).__name__
    known = {
        "PhoneNumberInvalidError": "Telegram doesn't know that phone number. Include the country code, like +61…",
        "PhoneCodeInvalidError": "That code isn't right. Check the latest one Telegram sent and try again.",
        "PhoneCodeExpiredError": "That code has expired. Ask for a new one.",
        "PasswordHashInvalidError": "That password isn't right.",
        "ApiIdInvalidError": "Telegram doesn't accept TELEGRAM_API_ID and TELEGRAM_API_HASH. Check them on my.telegram.org.",
        "FloodWaitError": f"Telegram asks to wait {getattr(e, 'seconds', 'a few')} seconds before trying again.",
        "AuthKeyUnregisteredError": "Telegram signed this session out. Sign in again on the Connections page.",
    }
    return known.get(name) or (str(e) if isinstance(e, TelegramError) else f"{name}: {e}")


def name_of(entity):
    """A person's or chat's name, as you'd say it."""
    if entity is None:
        return "Someone"
    title = getattr(entity, "title", None)
    if title:
        return title
    first, last = getattr(entity, "first_name", None) or "", getattr(entity, "last_name", None) or ""
    return " ".join(p for p in (first, last) if p) or (f"@{entity.username}" if getattr(entity, "username", None) else "Someone")


def when(date):
    """A message's time, the way people say it: 14:05, yesterday 18:00, Mon 9:30, 3 Feb."""
    if date is None:
        return ""
    local = date.replace(tzinfo=date.tzinfo or timezone.utc).astimezone()
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


def text_of(msg):
    """A message as text, with what's attached said in brackets."""
    parts = []
    for attr, label in (("voice", "voice message"), ("video_note", "video message"), ("sticker", "sticker"),
                        ("photo", "photo"), ("video", "video"), ("gif", "GIF"), ("audio", "audio"),
                        ("poll", "poll"), ("geo", "location"), ("contact", "contact")):
        if getattr(msg, attr, None):
            parts.append(f"[{label}]")
            break
    else:
        if getattr(msg, "document", None):
            file = getattr(msg, "file", None)
            parts.append(f"[file: {getattr(file, 'name', None) or 'unnamed'}]")
    text = (getattr(msg, "message", None) or "").strip()
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT - 1] + "…"
    if text:
        parts.append(text)
    return " ".join(parts) or "[empty]"


def _muted(dialog):
    until = getattr(getattr(getattr(dialog, "dialog", None), "notify_settings", None), "mute_until", None)
    return bool(until and until.replace(tzinfo=until.tzinfo or timezone.utc) > datetime.now(timezone.utc))


def _kind(dialog):
    return "person" if getattr(dialog, "is_user", False) else "channel" if getattr(dialog, "is_channel", False) and not getattr(dialog, "is_group", False) else "group"


def _entities(styles):
    """Telegram's formatting for core/texting.py's styles ("start:length:STYLE", in UTF-16 units, as Telegram counts)."""
    if not styles:
        return None
    from telethon.tl import types
    kinds = {"BOLD": types.MessageEntityBold, "ITALIC": types.MessageEntityItalic, "MONOSPACE": types.MessageEntityCode,
             "STRIKETHROUGH": types.MessageEntityStrike, "SPOILER": types.MessageEntitySpoiler}
    return [kinds[kind](offset=int(start), length=int(length))
            for start, length, kind in (s.split(":") for s in styles)]


class Telegram:
    """The signed-in account, if any, and the sign-in in progress."""

    def __init__(self, make_client=_client):
        self.make_client = make_client
        self.client = None
        self.made_with = None  # the keys the client was made with: new keys need a new client
        self.loop = None
        self.lock = threading.Lock()
        self.stage = None   # None until checked; else "signed_out", "code", "password" or "ready"
        self.me = None      # {name, username, phone} once signed in
        self.error = ""
        self.pending = {}   # phone and phone_code_hash while a code is on its way
        self.checking = False
        self.hear = None    # LISTEN's hear, while it listens
        self._dialogs = (0.0, [])

    @property
    def session(self):
        return paths.MEMORY_DIR / "telegram"  # Telethon adds .session

    # --- the client's own loop ---

    def _submit(self, coro, timeout=TIMEOUT):
        """Run a coroutine on the client's loop, and wait for it."""
        with self.lock:
            if self.loop is None:
                self.loop = asyncio.new_event_loop()
                threading.Thread(target=self.loop.run_forever, daemon=True, name="telegram").start()
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)
        except concurrent.futures.TimeoutError:
            raise TelegramError("Telegram didn't answer in time.")

    def _run(self, coro, timeout=TIMEOUT):
        """Connect (with the keys this thread may see), then run a coroutine that uses self.client."""
        keys = tuple(str(os.environ.get(k) or "").strip() for k in KEYS)
        return self._submit(self._with(keys, coro), timeout)

    async def _with(self, keys, coro):
        await self._connected(keys)
        return await coro

    async def _connected(self, keys):
        if keys != self.made_with and self.client is not None:  # its keys changed
            await self.client.disconnect()
            self.client = None
        if self.client is None:
            api_id, api_hash = keys
            if not (api_id and api_hash):
                raise TelegramError("Telegram isn't set up yet: paste your app's api_id and api_hash in the Telegram "
                                    "row on the Connections page.")
            if not api_id.isdigit():
                raise TelegramError("TELEGRAM_API_ID should be a number, from my.telegram.org.")
            self.session.parent.mkdir(parents=True, exist_ok=True)
            self.client, self.made_with = self.make_client(api_id, api_hash, self.session), keys
        if not self.client.is_connected():
            await self.client.connect()
        return self.client

    # --- signing in ---

    def status(self):
        """Where signing in is up to, for its row on the Connections page (SIGNIN). Cheap: the first call checks the
        saved session in the background."""
        if not all(os.environ.get(k) for k in KEYS):
            return {"status": "Add its app's keys first",
                    "help": "First, sign in at https://my.telegram.org, open API development tools and create an app "
                            "(any name). Paste its api_id and api_hash here."}
        if self.stage is None and not self.checking:
            self.checking = True
            threading.Thread(target=self._check, daemon=True).start()
        if self.stage is None:
            return {"status": "Checking…", "busy": True}
        if self.stage == "ready":
            me = self.me or {}
            who = " · ".join(p for p in (me.get("name"), f"@{me['username']}" if me.get("username") else None) if p)
            return {"ready": True, "who": who, "help": "Reading never marks your messages as read."}
        again = [{"action": "start_again", "label": "Start again"}]
        if self.stage == "code":
            return {"status": "Enter the code Telegram sent", "error": self.error,
                    "help": "Telegram sent a code to the Telegram app on your other devices (or by text).",
                    "fields": [{"name": "code", "label": "The code from Telegram", "kind": "number"}],
                    "submit": {"action": "sign_in", "label": "Sign in"}, "actions": again}
        if self.stage == "password":
            return {"status": "Enter your two-step password", "error": self.error,
                    "fields": [{"name": "password", "label": "Your Telegram password", "kind": "secret"}],
                    "submit": {"action": "password", "label": "Sign in"}, "actions": again}
        return {"status": "Not connected", "error": self.error,
                "help": "Telegram sends a code to the Telegram app on your other devices. Agent City signs in as you, "
                        "like a new Telegram app would.",
                "fields": [{"name": "phone", "label": "Phone number with country code", "kind": "phone"}],
                "submit": {"action": "send_code", "label": "Send code"}}

    def _check(self):
        """Whether the saved session is still signed in. Runs in a thread."""
        try:
            async def check():
                return await self.client.get_me() if await self.client.is_user_authorized() else None
            me = self._run(check()) if self.session.with_suffix(".session").exists() else None
            if me:
                self._signed_in(me)
            else:
                self.stage = "signed_out"
        except Exception as e:
            self.stage, self.error = "signed_out", _friendly(e)
        finally:
            self.checking = False

    def _signed_in(self, me):
        self.me = {"name": name_of(me), "username": getattr(me, "username", None) or "", "phone": getattr(me, "phone", "") or ""}
        self.stage, self.error, self.pending = "ready", "", {}
        try:
            os.chmod(self.session.with_suffix(".session"), 0o600)  # it's a sign-in: only you may read it
        except OSError:
            pass

    def act(self, action, values):
        """A button in its row on the Connections page (SIGNIN): the notice to show."""
        if action == "send_code":
            self.send_code(values.get("phone"))
            return "Telegram sent you a code, in the Telegram app on your other devices (or by text)."
        if action == "sign_in":
            if self.enter_code(values.get("code")) == "password":
                return "Your account has a two-step password: enter it to finish."
            return self._welcome()
        if action == "password":
            self.enter_password(values.get("password"))
            return self._welcome()
        if action == "start_again":
            self.cancel()
            return ""
        if action == "disconnect":
            self.sign_out()
            return "Disconnected Telegram."
        raise TelegramError("That isn't something Telegram's sign-in does.")

    def _welcome(self):
        who = (self.me or {}).get("name")
        return (f"Signed in to Telegram{' as ' + who if who else ''}. The Telegram agent is on, and tells you when "
                "something urgent arrives.")

    def send_code(self, phone):
        """Ask Telegram to send a sign-in code. TelegramError says what's wrong."""
        phone = "".join(c for c in str(phone or "") if c.isdigit() or c == "+")
        if len(phone.lstrip("+")) < 6:
            raise TelegramError("Enter your phone number with its country code, like +61 412 345 678.")
        try:
            sent = self._run(self._ask_code(phone))
        except Exception as e:
            self.error = _friendly(e)
            raise TelegramError(self.error)
        self.pending = {"phone": phone, "hash": sent.phone_code_hash}
        self.stage, self.error = "code", ""

    async def _ask_code(self, phone):
        return await self.client.send_code_request(phone)

    def enter_code(self, code):
        """Sign in with the code Telegram sent. Returns the new stage: ready, or password if two-step is on."""
        code = "".join(c for c in str(code or "") if c.isdigit())
        if not self.pending or not code:
            raise TelegramError("Ask for a code first." if not self.pending else "Enter the code Telegram sent.")
        try:
            self._signed_in(self._run(self._sign_in(phone=self.pending["phone"], code=code,
                                                    phone_code_hash=self.pending["hash"])))
        except Exception as e:
            if _password_needed(e):
                self.stage, self.error = "password", ""
                return self.stage
            self.error = _friendly(e)
            raise TelegramError(self.error)
        return self.stage

    async def _sign_in(self, **how):
        return await self.client.sign_in(**how)

    def enter_password(self, password):
        """Finish signing in with the two-step verification password."""
        if self.stage != "password" or not password:
            raise TelegramError("Enter your Telegram password.")
        try:
            self._signed_in(self._run(self._sign_in(password=str(password))))
        except Exception as e:
            self.error = _friendly(e)
            raise TelegramError(self.error)
        return self.stage

    def sign_out(self):
        """Sign out at Telegram too, and forget the session."""
        try:
            if self.stage == "ready":
                self._run(self._log_out())
        except Exception:
            pass  # gone already, or offline: forget it here anyway
        finally:
            if self.client is not None:
                try:
                    self._submit(self.client.disconnect(), timeout=10)
                except Exception:
                    pass
            self.client, self.made_with, self.me, self.pending, self.error = None, None, None, {}, ""
            self.stage = "signed_out"
            for suffix in (".session", ".session-journal"):
                try:
                    self.session.with_suffix(suffix).unlink()
                except FileNotFoundError:
                    pass

    async def _log_out(self):
        await self.client.log_out()

    def cancel(self):
        """Back to the start of signing in."""
        if self.stage in ("code", "password"):
            self.pending, self.error = {}, ""
            self.stage = "signed_out"

    # --- listening ---

    def listen(self, hear, stop):
        """Hear each new message until stop is set (LISTEN)."""
        from telethon import events
        handler = events.NewMessage(incoming=True)

        async def attach():
            self._dialogs = (time.time(), await self.client.get_dialogs(limit=200))  # which chats are muted
            self.client.add_event_handler(self._new_message, handler)

        self._ready()
        self._run(attach())
        self.hear = hear
        try:
            stop.wait()
        finally:
            self.hear = None
            if self.client is not None:
                self.client.remove_event_handler(self._new_message, handler)

    def quiet(self, chat_id, kind):
        """Whether a chat's messages never need you at once: a channel, or a muted chat (as the chats last read say)."""
        return kind == "channel" or any(d.id == chat_id and _muted(d) for d in self._dialogs[1])

    async def _new_message(self, event):
        """One new message arrived (on the client's loop): hand it on."""
        hear = self.hear
        if hear is None:
            return
        try:
            kind = "person" if event.is_private else "group" if event.is_group else "channel"
            msg = event.message
            chat, sender = await event.get_chat(), await event.get_sender()
            hear({"chat": name_of(chat), "id": f"{event.chat_id}/{msg.id}", "kind": kind, "sender": name_of(sender),
                  "text": text_of(msg), "mentioned": bool(getattr(msg, "mentioned", False)),
                  "quiet": self.quiet(event.chat_id, kind)})
        except Exception:
            pass  # one odd message mustn't stop the listening

    # --- reading and sending ---

    def _ready(self):
        if self.stage != "ready":
            raise TelegramError("Telegram isn't connected. Connect it on the Connections page.")

    def call(self, make):
        """Run make(client), a coroutine, once signed in. TelegramError says what's wrong."""
        self._ready()

        async def go():
            return await make(self.client)
        try:
            return self._run(go())
        except TelegramError:
            raise
        except Exception as e:
            if type(e).__name__ in ("AuthKeyUnregisteredError", "SessionRevokedError"):
                self.stage, self.me = "signed_out", None
            raise TelegramError(_friendly(e))

    def dialogs(self, fresh=False):
        """Your chats, newest first (remembered for half a minute)."""
        at, found = self._dialogs
        if fresh or time.time() - at > 30 or not found:
            found = self.call(lambda c: c.get_dialogs(limit=200))
            self._dialogs = (time.time(), found)
        return found

    def find(self, chat):
        """The chat called chat (a name, @username or id from unread_chats). TelegramError if there's none."""
        wanted = str(chat or "").strip()
        if not wanted:
            raise TelegramError("Say which chat.")
        dialogs = self.dialogs()
        bare = wanted.lstrip("@").lower()
        for match in (lambda d: str(d.id) == wanted,
                      lambda d: (getattr(d.entity, "username", None) or "").lower() == bare,
                      lambda d: (d.name or "").lower() == wanted.lower(),
                      lambda d: (d.name or "").lower().startswith(wanted.lower()),
                      lambda d: wanted.lower() in (d.name or "").lower()):
            found = [d for d in dialogs if match(d)]
            if len(found) == 1:
                return found[0]
            if found:
                names = ", ".join(f"{d.name} (id {d.id})" for d in found[:5])
                raise TelegramError(f"More than one chat matches '{wanted}': {names}. Use the id.")
        raise TelegramError(f"There's no chat called '{wanted}'. Use unread_chats to see chats and their ids.")


ACCOUNT = Telegram()
SIGNIN = {"status": lambda: ACCOUNT.status(), "act": lambda action, values: ACCOUNT.act(action, values)}


def LISTEN(hear, stop):
    ACCOUNT.listen(hear, stop)


def _tool(name, description, properties=None, required=()):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties or {}, "required": list(required)}}}


CHAT = {"type": "string", "description": "The chat: a person's or group's name, an @username, or its id from unread_chats."}
TOOLS = [
    _tool("unread_chats", "Chats with unread messages, newest first: who, how many unread, whether you're mentioned, "
          "and the latest message. Start here for 'anything new?' or 'anything urgent?'.",
          {"include_muted": {"type": "boolean", "description": "Also muted chats and channels (default false)."},
           "since_minutes": {"type": "integer", "description": "Only chats whose latest message came in the last this many "
                                                                 "minutes, for checking what's new since last time."},
           "limit": {"type": "integer", "description": "At most this many chats (default 15)."}}),
    _tool("read_chat", "Read a chat's latest messages, oldest first, each with its id; unread ones are marked new. "
          "Doesn't mark them as read.",
          {"chat": CHAT, "limit": {"type": "integer", "description": "How many messages (default 20, at most 60)."}},
          ["chat"]),
    _tool("search_messages", "Find messages containing some words, in one chat or all of them.",
          {"query": {"type": "string", "description": "Words to look for."},
           "chat": {**CHAT, "description": "Only this chat (leave out for all chats)."},
           "limit": {"type": "integer", "description": "At most this many (default 15)."}}, ["query"]),
    _tool("send_message", "Send a message as the user, to a person or group. The user may have to approve it first. "
          "Only when the user asked for a message to be sent; write it the way they would.",
          {"chat": CHAT, "text": {"type": "string", "description": "The message, exactly as it will be sent."},
           "reply_to": {"type": "integer", "description": "The id of a message to reply to, from read_chat (optional)."}},
          ["chat", "text"]),
]


def unread_chats(include_muted=False, since_minutes=None, limit=15):
    try:
        dialogs = ACCOUNT.dialogs(fresh=True)
    except Exception as e:
        return {"error": _friendly(e)}
    chats, muted_skipped = [], 0
    since = datetime.now(timezone.utc).timestamp() - int(since_minutes) * 60 if since_minutes else None
    for d in dialogs:
        if not d.unread_count and not getattr(d, "unread_mentions_count", 0):
            continue
        if since and (d.message is None or d.message.date.replace(tzinfo=d.message.date.tzinfo or timezone.utc).timestamp() < since):
            continue
        muted = _muted(d)
        if muted and not include_muted:
            muted_skipped += 1
            continue
        last = d.message
        chat = {"chat": d.name, "id": d.id, "kind": _kind(d), "unread": d.unread_count}
        if getattr(d, "unread_mentions_count", 0):
            chat["mentions_you"] = d.unread_mentions_count
        if muted:
            chat["muted"] = True
        if last is not None:
            sender = "You" if getattr(last, "out", False) else name_of(getattr(last, "sender", None)) if chat["kind"] == "group" else d.name
            chat["latest"] = f"{when(last.date)} · {sender}: {text_of(last)[:300]}"
        chats.append(chat)
    limit = max(1, min(int(limit or 15), 40))
    out = {"unread_chats": chats[:limit], "total_unread": sum(c["unread"] for c in chats)}
    if len(chats) > limit:
        out["more"] = f"{len(chats) - limit} more chats have unread messages."
    if muted_skipped:
        out["muted_skipped"] = f"{muted_skipped} muted chats or channels with unread messages weren't included."
    if not chats:
        out["note"] = f"Nothing new in the last {since_minutes} minutes." if since_minutes else "Nothing unread."
    return out


def read_chat(chat, limit=20):
    try:
        dialog = ACCOUNT.find(chat)
        limit = max(1, min(int(limit or 20), 60))
        messages = ACCOUNT.call(lambda c: c.get_messages(dialog.entity, limit=limit))
    except Exception as e:
        return {"error": _friendly(e)}
    seen = getattr(getattr(dialog, "dialog", None), "read_inbox_max_id", 0) or 0
    lines = []
    for m in reversed(list(messages)):
        who = "You" if m.out else name_of(getattr(m, "sender", None)) if _kind(dialog) != "person" else dialog.name
        new = " (new)" if not m.out and m.id > seen else ""
        reply = f" ↩{m.reply_to_msg_id}" if getattr(m, "reply_to_msg_id", None) else ""
        lines.append(f"[{m.id}] {when(m.date)} · {who}{new}{reply}: {text_of(m)}")
    return {"chat": dialog.name, "id": dialog.id, "kind": _kind(dialog), "messages": "\n".join(lines) or "(no messages)"}


def search_messages(query, chat=None, limit=15):
    query = str(query or "").strip()
    if not query:
        return {"error": "Say what to look for."}
    try:
        entity = ACCOUNT.find(chat).entity if chat else None
        limit = max(1, min(int(limit or 15), 40))
        found = ACCOUNT.call(lambda c: c.get_messages(entity, search=query, limit=limit))
    except Exception as e:
        return {"error": _friendly(e)}
    lines = []
    for m in found:
        where = name_of(getattr(m, "chat", None)) if entity is None else ""
        who = "You" if m.out else name_of(getattr(m, "sender", None))
        lines.append(f"[{m.id}] {when(m.date)} · {where + ' · ' if where else ''}{who}: {text_of(m)[:400]}")
    return {"query": query, "messages": "\n".join(lines) or "Nothing found."}


def send_message(chat, text, reply_to=None):
    text = unescape(text).strip()  # emoji a small model wrote as \ud83d… escapes
    if not text:
        return {"error": "Write the message to send."}
    try:
        reply_to = reply_id(reply_to)
    except ValueError as e:
        return {"error": str(e)}
    plain, styles = styled(text)  # the model's markdown, as Telegram's own bold and italic
    try:
        dialog = ACCOUNT.find(chat)
        quoted = "\n".join(f"> {line}" for line in text.splitlines())
        what = f"Reply in **{dialog.name}** (to message {reply_to})" if reply_to else f"Send to **{dialog.name}**"
        try:
            approve("Send", f"{what}:\n\n{quoted}")
        except NotApproved as e:
            return {"error": UNSENT[e.reason]}
        sent = ACCOUNT.call(lambda c: c.send_message(dialog.entity, plain, reply_to=reply_to, parse_mode=None,
                                                     formatting_entities=_entities(styles)))
    except Exception as e:
        return {"error": _friendly(e)}
    return {"sent": True, "chat": dialog.name, "id": getattr(sent, "id", None)}


HANDLERS = {"unread_chats": unread_chats, "read_chat": read_chat, "search_messages": search_messages,
            "send_message": send_message}
