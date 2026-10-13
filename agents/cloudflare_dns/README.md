# cloudflare_dns — Flare

Manages DNS on your Cloudflare account: domains, records, backups and DNSSEC.

Needs two values on the **Connections** page: `CLOUDFLARE_API_TOKEN` (an API
token with Zone:Read and DNS:Edit, plus Zone:Edit if you want to add domains)
and `CLOUDFLARE_ACCOUNT_ID`. Only zones in that account are visible.

| Tool | Does |
|---|---|
| `list_zones(search)` | Domains in the account, with status and plan |
| `get_zone(zone)` | One domain's status and nameservers |
| `create_zone(domain)` | Add a domain; returns the nameservers to set |
| `list_records(zone, type, name, search)` | A domain's DNS records |
| `create_record(zone, type, name, content, ttl, proxied, priority, comment)` | Add a record |
| `update_record(zone, record_id, ...)` | Change a record |
| `delete_record(zone, record_id)` | Delete a record |
| `export_records(zone)` | BIND zone file backup |
| `import_records(zone, bind_text)` | Import a BIND zone file |
| `get_dnssec(zone)` | DNSSEC status and DS record |
| `set_dnssec(zone, enabled)` | Enable or disable DNSSEC |

```bash
python3 chat.py cloudflare_dns                              # from the project root
python3 chat.py cloudflare_dns "List the records on example.com"
```
