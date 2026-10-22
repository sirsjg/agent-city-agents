"""Tools for the Signal agent: reads your Signal chats, says what's waiting and what's urgent, and replies for you.

Signal has no API for your own account, so this links Agent City to it as another device, the way Signal Desktop
links, with signal-cli (https://github.com/AsamK/signal-cli, installed with `brew install signal-cli`: its row on
the Connections page offers to). The row shows a QR code (SIGNIN, core/signin.py); you scan it in Signal on your
phone, under Settings › Linked devices. signal-cli keeps the link in Agent City's memory folder, under signal/
(readable only by you); Disconnect deletes it here, and the phone's Linked devices list removes it at Signal.

A linked device only gets messages from the moment it's linked, never the ones before, so this keeps its own copy of
what arrives (and of what you send, from any device) in signal.db, beside it. A message counts as unread until it's
read on any of your devices (Signal tells linked devices) or you write in that chat after it.

While it's on, signal-cli runs in its JSON-RPC mode on stdin and stdout (LISTEN, core/listening.py), and each new
message is heard as it arrives. Those from a person, or that mention you in a group, are judged for urgency, and
anything urgent is announced in Activity and on paired phones, unless that's switched off on its cog (ALERTS).
Automations that start when a message arrives hear every new message.
It never sends read receipts. Sending asks the user first (core/approval.py), unless they switched that off on its
cog (ASKS).
"""

import itertools
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime

from core import paths
from core.approval import NotApproved, approve
from core.texting import reply_id, styled, unescape

ASKS = "Ask before sending each message"
ALERTS = "Tell me when something urgent arrives"
STARTERS = [  # automations it offers to add from its settings (core/workflows.py starter)
    {"name": "Reply ideas for urgent Signal messages",
     "description": "When something urgent arrives on Signal, it suggests a short reply in your voice. It never sends one.",
     "trigger": {"type": "message", "from": "signal", "only": "urgent"},
     "steps": [{"kind": "agent", "name": "Suggest replies", "agent": "signal",
                "prompt": "These urgent messages just arrived:\n\n{{trigger}}\n\nFor each, say who it's from and what "
                          "they need, then suggest a short reply in my voice. Don't send anything."}]},
    {"name": "Signal catch-up",
     "description": "Every evening at 6, who's waiting on you on Signal, and what they want.",
     "trigger": {"type": "schedule", "cron": "0 18 * * *"},
     "steps": [{"kind": "agent", "name": "Catch up", "agent": "signal",
                "prompt": "What's unread on Signal? Say who's waiting on me and what they want, most important "
                          "first, in a line each. Skip chit-chat."}]},
]
TIMEOUT = 30  # seconds to wait for signal-cli to answer
LINK_WAIT = 300  # seconds to wait for the QR code to be scanned
LINK_POLL = 1  # seconds between looking whether it has been
LINK_SETTLE = 3  # seconds to give signal-cli once the account is there
KEEP_DAYS = 90  # messages older than this are forgotten
TEXT_LIMIT = 1200  # characters of one message shown to the model
PLACES = ("/opt/homebrew/bin/signal-cli", "/usr/local/bin/signal-cli")  # where Homebrew puts it, if PATH doesn't say
BREWS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
INSTALL_WAIT = 900  # seconds Homebrew may take to install it
INSTALL = "Signal needs signal-cli, which links Agent City to your account like Signal Desktop does. Press Install on the Connections page."
UNSENT = {"skipped": "The user skipped it, so it wasn't sent.", "unanswered": "Nobody answered, so it wasn't sent.",
          "no_window": "I can only read from here: sending needs Agent City's window, to ask you first."}


class SignalError(Exception):
    pass


def find_cli():
    """signal-cli's path, or None if it isn't installed."""
    return shutil.which("signal-cli") or next((p for p in PLACES if os.access(p, os.X_OK)), None)


def find_brew():
    """Homebrew's path, or None if it isn't installed."""
    return shutil.which("brew") or next((p for p in BREWS if os.access(p, os.X_OK)), None)


def _brew_install(brew):
    """Install signal-cli with Homebrew: "" if it worked, else what went wrong."""
    try:
        done = subprocess.run([brew, "install", "signal-cli"], capture_output=True, text=True, timeout=INSTALL_WAIT,
                              env={**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1", "NONINTERACTIVE": "1"})
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"Homebrew didn't finish: {e}"
    if done.returncode:
        last = [l for l in (done.stderr or done.stdout or "").splitlines() if l.strip()]
        return "Homebrew couldn't install signal-cli" + (f": {last[-1].strip()}" if last else ".")
    return ""


def _spawn(args):
    return subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            bufsize=1, encoding="utf-8", errors="replace")


def when(ms):
    """A message's time (milliseconds since 1970), the way people say it: 14:05, yesterday 18:00, Mon 9:30, 3 Feb."""
    if not ms:
        return ""
    local = datetime.fromtimestamp(ms / 1000).astimezone()
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


def text_of(data):
    """A Signal message (a dataMessage or sentMessage) as text: mentions as @names, what's attached in brackets."""
    text = data.get("message") or ""
    for m in sorted(data.get("mentions") or [], key=lambda m: m.get("start", 0), reverse=True):
        start, length = m.get("start", 0), m.get("length", 1)
        text = text[:start] + "@" + (m.get("name") or m.get("number") or "someone") + text[start + length:]
    parts = []
    for a in data.get("attachments") or []:
        kind = (a.get("contentType") or "").split("/")[0]
        label = {"image": "photo", "video": "video", "audio": "voice message"}.get(kind, "file")
        parts.append(f"[{label}: {a['filename']}]" if label == "file" and a.get("filename") else f"[{label}]")
    if data.get("sticker"):
        parts.append("[sticker]")
    text = text.strip()
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT - 1] + "…"
    if text:
        parts.append(text)
    return " ".join(parts)


class Store:
    """The messages Agent City has seen since it was linked: signal.db in its memory folder."""

    def __init__(self, path):
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS messages (chat TEXT NOT NULL, chat_name TEXT NOT NULL, kind TEXT "
                            "NOT NULL, sender TEXT NOT NULL, sender_id TEXT NOT NULL, ts INTEGER NOT NULL, text TEXT NOT "
                            "NULL, out INTEGER NOT NULL, mentioned INTEGER NOT NULL, read INTEGER NOT NULL, "
                            "PRIMARY KEY (chat, sender_id, ts))")
            self.db.execute("DELETE FROM messages WHERE ts < ?", ((time.time() - KEEP_DAYS * 86400) * 1000,))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def add(self, m):
        """Keep a message ({chat, chat_name, kind, sender, sender_id, ts, text, out, mentioned}). Sending marks what
        came before it in that chat as read, as it does in Signal. Returns whether it was new."""
        with self.lock, self.db:
            added = self.db.execute("INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                m["chat"], m["chat_name"], m["kind"], m["sender"], m["sender_id"], m["ts"], m["text"], int(m["out"]),
                int(m.get("mentioned", False)), int(m["out"]))).rowcount
            self.db.execute("UPDATE messages SET chat_name = ? WHERE chat = ?", (m["chat_name"], m["chat"]))
            if m["out"]:
                self.db.execute("UPDATE messages SET read = 1 WHERE chat = ? AND ts <= ?", (m["chat"], m["ts"]))
        return bool(added)

    def read(self, sender_id, ts):
        """A message was read on one of your devices: it and everything before it in its chat are read."""
        with self.lock, self.db:
            for (chat,) in self.db.execute("SELECT chat FROM messages WHERE sender_id = ? AND ts = ?", (sender_id, ts)).fetchall():
                self.db.execute("UPDATE messages SET read = 1 WHERE chat = ? AND ts <= ?", (chat, ts))

    def unread(self, since_ms=None):
        """[{chat, chat_name, kind, unread, mentions, latest}] for chats with unread messages, newest first."""
        with self.lock:
            rows = self.db.execute(
                "SELECT chat, chat_name, kind, COUNT(*), SUM(mentioned), MAX(ts) FROM messages WHERE read = 0 AND out = 0 "
                "GROUP BY chat HAVING MAX(ts) >= ? ORDER BY MAX(ts) DESC", (since_ms or 0,)).fetchall()
            out = []
            for chat, name, kind, count, mentions, ts in rows:
                last = self.db.execute("SELECT sender, text, ts, out FROM messages WHERE chat = ? ORDER BY ts DESC LIMIT 1",
                                       (chat,)).fetchone()
                out.append({"chat": chat, "chat_name": name, "kind": kind, "unread": count, "mentions": mentions or 0,
                            "latest": last})
            return out

    def chats(self):
        """[(chat, chat_name, kind)] for every chat, newest first."""
        with self.lock:
            return self.db.execute("SELECT chat, chat_name, kind FROM messages GROUP BY chat ORDER BY MAX(ts) DESC").fetchall()

    def history(self, chat, limit):
        """A chat's latest messages, oldest first: [(sender, sender_id, ts, text, out, read)]."""
        with self.lock:
            rows = self.db.execute("SELECT sender, sender_id, ts, text, out, read FROM messages WHERE chat = ? "
                                   "ORDER BY ts DESC LIMIT ?", (chat, limit)).fetchall()
        return rows[::-1]

    def search(self, query, chat, limit):
        """Messages containing query, newest first: [(chat_name, sender, ts, text, out)]."""
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self.lock:
            return self.db.execute(
                "SELECT chat_name, sender, ts, text, out FROM messages WHERE text LIKE ? ESCAPE '\\' "
                + ("AND chat = ? " if chat else "") + "ORDER BY ts DESC LIMIT ?",
                (like, chat, limit) if chat else (like, limit)).fetchall()

    def forget(self):
        with self.lock, self.db:
            self.db.execute("DELETE FROM messages")


class Signal:
    """The linked account, if any, signal-cli running for it, and the link in progress."""

    def __init__(self, find=find_cli, spawn=_spawn, brew=find_brew, brew_install=_brew_install):
        self.find, self.spawn, self.brew, self.brew_install = find, spawn, brew, brew_install
        self._store = None
        self.lock = threading.Lock()      # one request written at a time
        self.starting = threading.RLock()  # one signal-cli started (or stopped) at a time
        self.proc = None
        self.ids = itertools.count(1)
        self.waiting = {}      # request id -> [Event, response]
        self.stage = ""        # "installing" or "linking" while that's going on
        self.error = ""
        self.qr = ""           # the sgnl://linkdevice address to show as a QR code while linking
        self.stopping = False
        self.hear = None       # LISTEN's hear, while it listens
        self.groups = {}       # group id -> name
        self.contacts = {}     # number or uuid -> name
        self.people = {}       # each contact once, by the id a chat with them has: number, else uuid -> name
        self.stderr = []       # signal-cli's last few complaints, for saying what went wrong

    @property
    def folder(self):
        return paths.MEMORY_DIR / "signal"

    @property
    def store(self):
        if self._store is None:
            self._store = Store(paths.MEMORY_DIR / "signal.db")
        return self._store

    def linked(self):
        """{number, aci} of the account signal-cli has linked in its folder, or None. signal-cli's own list of accounts
        says so as soon as a link is made: its answer to finishLink can be a long time coming, or never come."""
        try:
            accounts = json.loads((self.folder / "data" / "accounts.json").read_text()).get("accounts") or []
        except (OSError, ValueError, AttributeError):
            return None
        found = next((a for a in accounts if isinstance(a, dict) and (a.get("number") or a.get("uuid"))), None)
        return {"number": found.get("number"), "aci": found.get("uuid")} if found else None

    # --- signal-cli ---

    def _start(self, *args):
        """Start signal-cli in JSON-RPC mode (on the linked account unless linking). SignalError if it can't."""
        cli = self.find()
        if not cli:
            raise SignalError(INSTALL)
        with self.starting:
            self._stop()
            self.folder.mkdir(parents=True, exist_ok=True)
            os.chmod(self.folder, 0o700)  # it holds the link's keys: only you may read it
            self.stopping, self.stderr = False, []
            try:
                proc = self.spawn([cli, "--config", str(self.folder), "-o", "json", *args])
            except OSError as e:
                raise SignalError(f"signal-cli didn't start: {e}")
            self.proc = proc
        complaints = threading.Thread(target=self._complaints, args=(proc,), daemon=True, name="signal-err")
        complaints.start()
        threading.Thread(target=self._read, args=(proc, complaints), daemon=True, name="signal").start()

    def _stop(self):
        with self.starting:
            proc, self.proc = self.proc, None
        if proc is None:
            return
        self.stopping = True
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass

    def _complaints(self, proc):
        for line in proc.stderr:
            self.stderr = (self.stderr + [line.strip()])[-5:]

    def _read(self, proc, complaints):
        """Every line signal-cli writes: answers to requests, and messages as they arrive."""
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if "id" in msg and msg["id"] in self.waiting:
                slot = self.waiting[msg["id"]]
                slot[1] = msg
                slot[0].set()
            elif msg.get("method") == "receive":
                try:
                    self._envelope((msg.get("params") or {}).get("envelope") or {})
                except Exception:
                    pass  # one odd message mustn't stop the listening
        for slot in list(self.waiting.values()):
            slot[0].set()  # anyone still waiting hears it stopped
        if proc is self.proc and not self.stopping:  # it stopped by itself: LISTEN notices, and starts it again
            complaints.join(1)  # its last words say why
            self.proc = None
            why = next((l for l in reversed(self.stderr) if l), "")
            self.error = "signal-cli stopped" + (f": {why}" if why else ".")

    def call(self, method, params=None, timeout=TIMEOUT):
        """signal-cli's result for one request. SignalError says what's wrong."""
        proc = self.proc
        if proc is None:
            raise SignalError("Signal isn't running right now. Try again in a minute, or check its row on the "
                              "Connections page.")
        id = str(next(self.ids))
        slot = self.waiting[id] = [threading.Event(), None]
        try:
            with self.lock:
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}, "id": id}) + "\n")
                proc.stdin.flush()
            if not slot[0].wait(timeout):
                raise SignalError("signal-cli didn't answer in time.")
        except (OSError, ValueError):
            raise SignalError("signal-cli stopped.")
        finally:
            self.waiting.pop(id, None)
        answer = slot[1]
        if answer is None:
            raise SignalError(self.error or "signal-cli stopped.")
        if answer.get("error"):
            raise SignalError(f"Signal said: {answer['error'].get('message') or answer['error']}")
        return answer.get("result")

    def _account_args(self, me):
        return ["-a", me.get("number") or me["aci"], "jsonRpc", "--ignore-attachments"]

    # --- linking ---

    def status(self):
        """Where linking is up to, for its row on the Connections page (SIGNIN). Cheap."""
        if self.stage == "installing":
            return {"status": "Installing…", "busy": True, "help": "Installing signal-cli. This takes a minute or two…"}
        if self.stage == "linking":
            return {"status": "Scan the QR code", "qr": self.qr, "busy": not self.qr,
                    "steps": ["Open Signal on your phone.",
                              "iPhone: tap your profile picture, then Linked devices. Android: tap ⋮, then Linked devices.",
                              "Tap Link new device and point the camera at this code."],
                    "actions": [{"action": "cancel", "label": "Cancel"}]}
        if not self.find():
            brew = self.brew()
            return {"status": "Needs one install first", "error": self.error,
                    "help": "Agent City links to Signal with a free helper, signal-cli (https://github.com/AsamK/signal-cli). "
                            + ("Press Install to add it (about 120 MB, a minute or two)." if brew else
                               "It installs with Homebrew, which isn't on this Mac yet: install Homebrew from "
                               "https://brew.sh (copy the one line it shows into Terminal), then press Check again."),
                    "actions": ([{"action": "install", "label": "Install"}] if brew else [])
                               + [{"action": "check", "label": "Check again"}]}
        me = self.linked()
        if me:
            return {"ready": True, "who": me.get("number") or "", "error": self.error if self.proc is None else "",
                    "help": "It never sends read receipts."}
        return {"status": "Not connected", "error": self.error,
                "help": "Links Agent City to your Signal account as another device, like Signal Desktop. Have your "
                        "phone ready: you'll scan a QR code with it. Agent City sees messages from then on, not older ones.",
                "actions": [{"action": "link", "label": "Connect"}]}

    def act(self, action, values):
        """A button in its row on the Connections page (SIGNIN): the notice to show."""
        if action == "install":
            self.install()
            return "signal-cli is installed. Press Connect to link your Signal account."
        if action == "check":
            self.error = ""
            return "" if self.find() else "signal-cli still isn't installed."
        if action == "link":
            self.link()
            who = (self.linked() or {}).get("number")
            return (f"Linked to Signal{' as ' + who if who else ''}. The Signal agent is on, and tells you when "
                    "something urgent arrives. It only sees messages from now on.")
        if action == "cancel":
            self.cancel()
            return ""
        if action == "disconnect":
            self.unlink()
            return "Disconnected Signal. On your phone, open Signal › Settings › Linked devices and remove Agent City too."
        raise SignalError("That isn't something Signal's sign-in does.")

    def install(self):
        """Install signal-cli with Homebrew. Takes minutes. SignalError if it can't."""
        if self.find():
            return
        brew = self.brew()
        if not brew:
            self.error = "Homebrew isn't installed. Install it from https://brew.sh first, then press Check again."
            raise SignalError(self.error)
        self.stage, self.error = "installing", ""
        try:
            problem = self.brew_install(brew)
        finally:
            self.stage = ""
        if problem or not self.find():
            self.error = problem or "signal-cli still isn't there after installing it."
            raise SignalError(self.error)

    def link(self):
        """Link: the QR code to scan is in status() until it's scanned or it runs out. Waits for the scan."""
        if self.linked():
            return
        self.stage, self.qr, self.error = "linking", "", ""
        try:
            self._start("jsonRpc")  # not on an account yet: that's how signal-cli offers startLink and finishLink
            self.qr = self.call("startLink")["deviceLinkUri"]
        except (SignalError, KeyError, TypeError) as e:
            self._stop()
            self.stage, self.error = "", str(e) or "Linking didn't start."
            raise SignalError(self.error)
        finished = []  # finishLink's answer or failure, if it comes

        def finish():
            try:
                finished.append(self.call("finishLink", {"deviceLinkUri": self.qr, "deviceName": "Agent City"},
                                          timeout=LINK_WAIT))
            except SignalError as e:
                finished.append(e)
        threading.Thread(target=finish, daemon=True).start()
        end = time.time() + LINK_WAIT
        while self.stage == "linking" and not finished and not self.linked() and time.time() < end:
            time.sleep(LINK_POLL)
        cancelled = self.stage != "linking"
        self.stage, self.qr = "", ""
        if cancelled:
            self._stop()
            raise SignalError("")  # Cancel was pressed: nothing to say
        if not self.linked():
            self._stop()
            problem = finished[0] if finished and isinstance(finished[0], SignalError) else None
            self.error = (f"Linking didn't work: {problem}" if problem and "in time" not in str(problem)
                          else "The QR code ran out before it was scanned. Press Connect to get a new one.")
            raise SignalError(self.error)
        time.sleep(LINK_SETTLE)  # let signal-cli finish saving the new account before it's stopped
        self._stop()  # LISTEN starts it again, on the account

    def cancel(self):
        """Stop linking."""
        if self.stage == "linking":
            self.stage, self.qr, self.error = "", "", ""
            self._stop()

    def unlink(self):
        """Forget the link and everything kept here. The phone keeps listing Agent City under Linked devices until
        it's removed there."""
        self._stop()
        self.qr, self.error = "", ""
        shutil.rmtree(self.folder, ignore_errors=True)
        self.store.forget()

    # --- hearing messages ---

    def listen(self, hear, stop):
        """Run signal-cli on the linked account and hear each new message, until stop is set (LISTEN). SignalError if
        it stops by itself: Agent City starts LISTEN again a minute later."""
        me = self.linked()
        if not me:
            raise SignalError("Signal isn't linked.")
        self._start(*self._account_args(me))
        self.error, self.hear = "", hear
        try:
            try:
                self.call("sendSyncRequest")  # ask the phone for contacts and groups, so names are known
            except SignalError:
                pass
            self._names()
            while not stop.wait(1):
                if self.proc is None:
                    raise SignalError(self.error or "signal-cli stopped.")
        finally:
            self.hear = None
            self._stop()

    def _names(self):
        """Contacts' and groups' names, from what signal-cli knows."""
        try:
            for g in self.call("listGroups") or []:
                if g.get("id"):
                    self.groups[g["id"]] = g.get("name") or "Unnamed group"
            for c in self.call("listContacts") or []:
                profile = c.get("profile") or {}
                name = (c.get("name") or " ".join(p for p in (profile.get("givenName"), profile.get("familyName")) if p)
                        or c.get("username"))
                for key in (c.get("number"), c.get("uuid")):
                    if key and name:
                        self.contacts[key] = name
                if name and (c.get("number") or c.get("uuid")):
                    self.people[c.get("number") or c.get("uuid")] = name
        except (SignalError, AttributeError, TypeError):
            pass

    def _mine(self, number, uuid):
        me = self.linked() or {}
        return bool((number and number == me.get("number")) or (uuid and uuid == me.get("aci")))

    def _envelope(self, env):
        """One thing signal-cli received: a message, a copy of one you sent from another device, or a read receipt."""
        sync = env.get("syncMessage") or {}
        for r in sync.get("readMessages") or []:
            self.store.read(r.get("senderNumber") or r.get("senderUuid") or r.get("sender") or "", r.get("timestamp"))
        sent = sync.get("sentMessage")
        if sent:
            text = text_of(sent)
            if not text:
                return
            group = (sent.get("groupInfo") or {}).get("groupId")
            to = sent.get("destinationNumber") or sent.get("destinationUuid") or sent.get("destination")
            if not (group or to):
                return
            self.store.add({"chat": f"group:{group}" if group else to, "kind": "group" if group else "person",
                            "chat_name": self._group_name(group) if group else self.contacts.get(to) or to,
                            "sender": "You", "sender_id": "me", "ts": sent.get("timestamp") or env.get("timestamp"),
                            "text": text, "out": True})
            return
        data = env.get("dataMessage")
        if not data or data.get("reaction") or data.get("remoteDelete"):
            return
        text = text_of(data)
        if not text:
            return
        sender_id = env.get("sourceNumber") or env.get("sourceUuid") or env.get("source") or ""
        sender = env.get("sourceName") or self.contacts.get(sender_id) or sender_id
        if sender_id and env.get("sourceName"):
            self.contacts[sender_id] = env["sourceName"]
        group = (data.get("groupInfo") or {}).get("groupId")
        mentioned = any(self._mine(m.get("number"), m.get("uuid")) for m in data.get("mentions") or [])
        m = {"chat": f"group:{group}" if group else sender_id, "kind": "group" if group else "person",
             "chat_name": self._group_name(group) if group else sender, "sender": sender, "sender_id": sender_id,
             "ts": data.get("timestamp") or env.get("timestamp"), "text": text, "out": False, "mentioned": mentioned}
        if not self.store.add(m):
            return
        hear = self.hear
        if hear is not None:
            hear({"chat": m["chat_name"], "id": f"{m['chat']}/{m['ts']}", "kind": m["kind"], "sender": sender,
                  "text": text, "mentioned": mentioned})

    def _group_name(self, group):
        if group not in self.groups:
            threading.Thread(target=self._names, daemon=True).start()  # a new group: learn its name for next time
        return self.groups.get(group) or "A group"

    # --- finding chats ---

    def find_chat(self, chat):
        """(chat id, name, kind) for chat (a name, number or id from unread_chats). SignalError if there's none."""
        wanted = str(chat or "").strip()
        if not wanted:
            raise SignalError("Say which chat.")
        known = {c: (c, n, k) for c, n, k in self.store.chats()}
        for key, name in self.people.items():  # contacts and groups not heard from yet
            known.setdefault(key, (key, name, "person"))
        for key, name in self.groups.items():
            known.setdefault(f"group:{key}", (f"group:{key}", name, "group"))
        low = wanted.lower()
        if wanted.startswith("+") and wanted[1:].replace(" ", "").isdigit():
            known.setdefault(wanted.replace(" ", ""), (wanted.replace(" ", ""), wanted, "person"))  # anyone, by number
        for match in (lambda c: c[0] == wanted or c[0] == f"group:{wanted}",
                      lambda c: c[0].replace(" ", "") == wanted.replace(" ", ""),
                      lambda c: c[1].lower() == low,
                      lambda c: c[1].lower().startswith(low),
                      lambda c: low in c[1].lower()):
            found = list(dict.fromkeys(c for c in known.values() if match(c)))
            if len(found) == 1:
                return found[0]
            if found:
                names = ", ".join(f"{n} (id {c})" for c, n, _ in found[:5])
                raise SignalError(f"More than one chat matches '{wanted}': {names}. Use the id.")
        raise SignalError(f"There's no chat called '{wanted}' that Agent City has seen. Use unread_chats to see chats "
                          "and their ids, or give a phone number with its country code.")


ACCOUNT = Signal()
SIGNIN = {"status": lambda: ACCOUNT.status(), "act": lambda action, values: ACCOUNT.act(action, values)}


def LISTEN(hear, stop):
    ACCOUNT.listen(hear, stop)


def _tool(name, description, properties=None, required=()):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties or {}, "required": list(required)}}}


CHAT = {"type": "string", "description": "The chat: a person's or group's name, a phone number, or its id from unread_chats."}
TOOLS = [
    _tool("unread_chats", "Chats with unread messages, newest first: who, how many unread, whether you're mentioned, "
          "and the latest message. Start here for 'anything new?' or 'anything urgent?'.",
          {"since_minutes": {"type": "integer", "description": "Only chats with a message in the last this many minutes."},
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


def unread_chats(since_minutes=None, limit=15):
    since = (time.time() - int(since_minutes) * 60) * 1000 if since_minutes else None
    chats = []
    for c in ACCOUNT.store.unread(since):
        chat = {"chat": c["chat_name"], "id": c["chat"], "kind": c["kind"], "unread": c["unread"]}
        if c["mentions"]:
            chat["mentions_you"] = c["mentions"]
        sender, text, ts, out = c["latest"]
        chat["latest"] = f"{when(ts)} · {sender}: {text[:300]}"
        chats.append(chat)
    limit = max(1, min(int(limit or 15), 40))
    out = {"unread_chats": chats[:limit], "total_unread": sum(c["unread"] for c in chats)}
    if len(chats) > limit:
        out["more"] = f"{len(chats) - limit} more chats have unread messages."
    if not chats:
        out["note"] = f"Nothing new in the last {since_minutes} minutes." if since_minutes else "Nothing unread."
    if ACCOUNT.proc is None:
        out["note"] = "Signal isn't running right now, so this is only what arrived while it was."
    return out


def read_chat(chat, limit=20):
    try:
        id, name, kind = ACCOUNT.find_chat(chat)
    except SignalError as e:
        return {"error": str(e)}
    rows = ACCOUNT.store.history(id, max(1, min(int(limit or 20), 60)))
    lines = [f"[{ts}] {when(ts)} · {'You' if out else sender}{'' if out or read else ' (new)'}: {text}"
             for sender, _, ts, text, out, read in rows]
    return {"chat": name, "id": id, "kind": kind,
            "messages": "\n".join(lines) or "(no messages since Agent City was linked)"}


def search_messages(query, chat=None, limit=15):
    query = str(query or "").strip()
    if not query:
        return {"error": "Say what to look for."}
    try:
        id = ACCOUNT.find_chat(chat)[0] if chat else None
    except SignalError as e:
        return {"error": str(e)}
    found = ACCOUNT.store.search(query, id, max(1, min(int(limit or 15), 40)))
    lines = [f"{when(ts)} · {'' if id else name + ' · '}{'You' if out else sender}: {text[:400]}"
             for name, sender, ts, text, out in found]
    return {"query": query, "messages": "\n".join(lines) or "Nothing found since Agent City was linked."}


def send_message(chat, text, reply_to=None):
    text = unescape(text).strip()  # emoji a small model wrote as \ud83d… escapes
    if not text:
        return {"error": "Write the message to send."}
    try:
        reply_to = reply_id(reply_to)
    except ValueError as e:
        return {"error": str(e)}
    plain, styles = styled(text)  # Signal shows **bold** as asterisks: it takes styles beside the text instead
    try:
        id, name, kind = ACCOUNT.find_chat(chat)
        params = {"groupId": id[len("group:"):]} if id.startswith("group:") else {"recipient": [id]}
        params["message"] = plain
        if styles:
            params["textStyle"] = styles
        if reply_to:
            quoted = next((r for r in ACCOUNT.store.history(id, 200) if r[2] == reply_to), None)
            if quoted is None:
                raise SignalError(f"There's no message {reply_to} in {name}. Use the ids from read_chat.")
            me = ACCOUNT.linked() or {}
            params.update({"quoteTimestamp": reply_to,
                           "quoteAuthor": (me.get("number") or me.get("aci")) if quoted[4] else quoted[1]})
        lines = "\n".join(f"> {line}" for line in text.splitlines())
        what = f"Reply in **{name}** (to message {reply_to})" if reply_to else f"Send to **{name}**"
        try:
            approve("Send", f"{what} on Signal:\n\n{lines}")
        except NotApproved as e:
            return {"error": UNSENT[e.reason]}
        sent = ACCOUNT.call("send", params)
    except SignalError as e:
        return {"error": str(e)}
    ts = (sent or {}).get("timestamp") or int(time.time() * 1000)
    ACCOUNT.store.add({"chat": id, "chat_name": name, "kind": kind, "sender": "You", "sender_id": "me",
                       "ts": ts, "text": plain, "out": True})
    return {"sent": True, "chat": name, "id": ts}


HANDLERS = {"unread_chats": unread_chats, "read_chat": read_chat, "search_messages": search_messages,
            "send_message": send_message}
