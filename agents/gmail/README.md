# Gmail

Searches and reads your Gmail, and saves or sends emails, through your own Google account.

**Tools:** `search_messages`, `read_message`, `create_draft`, `send_message`.

**Setup:** it signs in with OAuth instead of a key. On the **Connections** page open the Google
*Setup guide* (register an app once, enable the **Gmail API** in your Google Cloud project, and paste
its client ID and secret), then press **Connect**. It asks only for `gmail.readonly` and
`gmail.compose`, so it can't delete or relabel mail.

Emails it reads are treated as information, never as instructions.
