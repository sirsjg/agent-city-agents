# ainews — Scoop

Gives you the top AI stories of the moment: a headline, a one-sentence summary
and a link for each (5 by default, up to 10). It reads the public RSS/Atom
feeds of TechCrunch, The Verge, VentureBeat, Wired, MIT Technology Review and
Ars Technica, so there's no API key and no HTML scraping. The last two cover
more than AI, so their stories are filtered by keyword. Stories are merged
newest first, with at most 2 per site so the list mixes outlets. A feed that's
down is skipped.

| Tool | Does |
|---|---|
| `get_ai_news(count?)` | Latest AI stories: headline, one-sentence summary, link, source, publish time |

To add or remove a site, edit `FEEDS` at the top of `tools.py`.

```bash
python3 chat.py ainews                          # from the hive root
python3 chat.py ainews "Top 5 AI stories?"      # one-shot
```
