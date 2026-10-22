# signal — Signal

Reads your own Signal account, linked to Agent City as another device (like Signal Desktop) with
[signal-cli](https://github.com/AsamK/signal-cli). Tells you what's waiting and what's urgent, and replies as you
after you approve.

To set it up, open **Connections** in Agent City. If signal-cli isn't installed, the Signal row offers to install
it with Homebrew (about 120 MB). Then press **Connect** and scan the QR code in Signal on your phone, under
**Settings › Linked devices › Link new device**. The link is kept in Agent City's memory folder; **Disconnect**
deletes it there, and you remove Agent City from the phone's Linked devices list too.

A linked device only gets messages from the moment it's linked, so the agent keeps its own copy of what arrives
(in `signal.db`, for 90 days). It never sends read receipts. While it's connected it listens for new messages:
automations can start from them, and urgent ones are announced in Activity and on paired phones, unless you switch
that off on its cog.

| Tool | Does |
|---|---|
| `unread_chats(since_minutes, limit)` | Chats with unread messages, newest first, with mentions and the latest message |
| `read_chat(chat, limit)` | A chat's latest messages, oldest first, new ones marked |
| `search_messages(query, chat, limit)` | Messages containing some words, in one chat or all |
| `send_message(chat, text, reply_to)` | Sends as you, once you approve (`ASKS`: switch that off on its cog) |

Needs Agent City 0.28.0 or later, for its sign-in row (`SIGNIN`).
