# abcau — Dispatch

Gives you the latest general news from ABC News (Australia): a headline, a
one-sentence summary and a link for each (5 by default, up to 10). It reads the
public Top Stories RSS feed, so there's no API key and no HTML scraping. Top
Stories is general news (Australian and world), not a tech feed. Stories come
newest first.

| Tool | Does |
|---|---|
| `get_headlines(count?)` | Latest headlines: headline, one-sentence summary, link, publish time |

I first tried news.com.au, but I couldn't confirm its feed addresses. To use a
different ABC feed, change `FEED_URL` at the top of `tools.py`.

```bash
python3 chat.py abcau                          # from the hive root
python3 chat.py abcau "What's in the news?"    # one-shot
```
