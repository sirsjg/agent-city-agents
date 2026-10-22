# telegram — Telegram

Reads your own Telegram account (not a bot) with [Telethon](https://docs.telethon.dev), Telegram's official user
API, which comes with Agent City. Tells you what's waiting and what's urgent, and replies as you after you approve.

To set it up, sign in at https://my.telegram.org, open **API development tools** and create an app (any name). In
Agent City, open **Connections**, paste its api_id and api_hash in the Telegram row (`TELEGRAM_API_ID`,
`TELEGRAM_API_HASH`), then enter your phone number, the code Telegram sends to your other devices, and your
two-step password if you have one. The session stays in Agent City's memory folder; **Disconnect** ends it at
Telegram too.

While it's connected it listens for new messages: automations can start from them, and urgent ones (a person who
needs you soon) are announced in Activity and on paired phones, unless you switch that off on its cog. Reading never
marks anything as read.

| Tool | Does |
|---|---|
| `unread_chats(include_muted, since_minutes, limit)` | Chats with unread messages, newest first, with mentions and the latest message |
| `read_chat(chat, limit)` | A chat's latest messages, oldest first, new ones marked |
| `search_messages(query, chat, limit)` | Messages containing some words, in one chat or all |
| `send_message(chat, text, reply_to)` | Sends as you, once you approve (`ASKS`: switch that off on its cog) |

Needs Agent City 0.28.0 or later, for its sign-in row (`SIGNIN`).
