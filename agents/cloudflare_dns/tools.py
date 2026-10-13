"""Tools for the cloudflare_dns agent.

Each tool has two parts:
  - a schema in OpenAI function-calling format (listed in TOOLS)
  - a Python implementation (registered in HANDLERS, keyed by the same name)

Talks to the Cloudflare v4 API (https://developers.cloudflare.com/api/) with an
API token and account ID from the Connections page. Zones are given by domain
name ("example.com"); the zone ID is looked up for you.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

NEEDS = {
    "CLOUDFLARE_API_TOKEN": "Create an API token at https://dash.cloudflare.com/profile/api-tokens "
                            "(template 'Edit zone DNS', or custom: Zone:Read, DNS:Edit, Zone:Edit to add zones)",
    "CLOUDFLARE_ACCOUNT_ID": "Your account ID: open any domain at https://dash.cloudflare.com, "
                             "it's in the right-hand sidebar under 'Account ID'",
}

API = "https://api.cloudflare.com/client/v4"
RECORD_TYPES = ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "SRV", "CAA", "PTR", "HTTPS", "SVCB"]
PROXIABLE = ("A", "AAAA", "CNAME")


def _call(method, path, params=None, body=None, raw=False):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        try:
            errs = json.loads(text).get("errors") or []
            text = "; ".join(f"{x.get('message')} (code {x.get('code')})" for x in errs) or text
        except ValueError:
            pass
        raise RuntimeError(f"Cloudflare said {e.code}: {text[:300]}")
    if raw:
        return payload.decode()
    return json.loads(payload)


def _result(method, path, **kw):
    return _call(method, path, **kw).get("result")


def _zone(zone):
    """Resolve a domain name (or a 32-character zone ID) to its zone."""
    zone = (zone or "").strip().lower().rstrip(".")
    if not zone:
        raise ValueError("Say which domain, e.g. 'example.com'.")
    if len(zone) == 32 and all(c in "0123456789abcdef" for c in zone):
        return _result("GET", f"/zones/{zone}")
    found = _result("GET", "/zones", params={"name": zone, "account.id": os.environ["CLOUDFLARE_ACCOUNT_ID"]})
    if not found:
        raise ValueError(f"No zone called '{zone}' in this account. Use list_zones to see them.")
    return found[0]


def _fqdn(name, zone_name):
    """'www' or '@' -> 'www.example.com' / 'example.com'; full names are kept."""
    name = (name or "").strip().lower().rstrip(".")
    if name in ("", "@"):
        return zone_name
    if name == zone_name or name.endswith("." + zone_name):
        return name
    return f"{name}.{zone_name}"


def _record(r):
    out = {k: r.get(k) for k in ("id", "type", "name", "content", "ttl", "proxied", "priority", "comment")
           if r.get(k) not in (None, "")}
    out["ttl"] = "auto" if r.get("ttl") == 1 else r.get("ttl")
    if r.get("data") and not r.get("content"):
        out["content"] = json.dumps(r["data"])
    return out


def _build(zone_name, type, name, content, ttl, proxied, priority, comment):
    body = {}
    if type:
        body["type"] = type.upper()
        if body["type"] not in RECORD_TYPES:
            raise ValueError(f"Record type must be one of {', '.join(RECORD_TYPES)}.")
    if name is not None:
        body["name"] = _fqdn(name, zone_name)
    if content is not None:
        body["content"] = content
    if ttl is not None:
        ttl = int(ttl)
        if ttl != 1 and not 30 <= ttl <= 86400:
            raise ValueError("ttl is 1 (automatic) or 30-86400 seconds (proxied records are always automatic).")
        body["ttl"] = ttl
    if proxied is not None:
        body["proxied"] = bool(proxied)
    if priority is not None:
        body["priority"] = int(priority)
    if comment is not None:
        body["comment"] = comment
    return body


# --- zones --------------------------------------------------------------------

def list_zones(search=None):
    zones = _result("GET", "/zones", params={
        "account.id": os.environ["CLOUDFLARE_ACCOUNT_ID"], "name": f"contains:{search}" if search else None,
        "per_page": 50, "order": "name"})
    return {"count": len(zones), "zones": [
        {"name": z["name"], "status": z["status"], "plan": z.get("plan", {}).get("name"),
         "paused": z.get("paused"), "nameservers": ", ".join(z.get("name_servers") or [])}
        for z in zones]}


def get_zone(zone):
    z = _zone(zone)
    return {"name": z["name"], "id": z["id"], "status": z["status"], "paused": z.get("paused"),
            "plan": z.get("plan", {}).get("name"), "type": z.get("type"),
            "nameservers": z.get("name_servers"), "original_nameservers": z.get("original_name_servers"),
            "created": z.get("created_on")}


def create_zone(domain):
    z = _result("POST", "/zones", body={
        "name": domain.strip().lower(), "account": {"id": os.environ["CLOUDFLARE_ACCOUNT_ID"]}, "type": "full"})
    return {"created": z["name"], "status": z["status"],
            "next_step": "Set the domain's nameservers at the registrar to: " + ", ".join(z.get("name_servers") or [])}


# --- records ------------------------------------------------------------------

def list_records(zone, type=None, name=None, search=None):
    z = _zone(zone)
    params = {"per_page": 100, "order": "type"}
    if type:
        params["type"] = type.upper()
    if name:
        params["name"] = _fqdn(name, z["name"])
    if search:
        params["search"] = search
    records = _result("GET", f"/zones/{z['id']}/dns_records", params=params)
    rows = [_record(r) for r in records]
    return {"zone": z["name"], "count": len(rows), "records": rows}


def create_record(zone, type, name, content, ttl=1, proxied=None, priority=None, comment=None):
    z = _zone(zone)
    body = _build(z["name"], type, name, content, ttl, proxied, priority, comment)
    if body["type"] in ("MX", "SRV") and "priority" not in body:
        body["priority"] = 10 if body["type"] == "MX" else 0
    if body.get("proxied") and body["type"] not in PROXIABLE:
        raise ValueError(f"{body['type']} records can't be proxied.")
    return {"created": _record(_result("POST", f"/zones/{z['id']}/dns_records", body=body))}


def update_record(zone, record_id, type=None, name=None, content=None, ttl=None,
                  proxied=None, priority=None, comment=None):
    z = _zone(zone)
    body = _build(z["name"], type, name, content, ttl, proxied, priority, comment)
    if not body:
        raise ValueError("Nothing to change: give at least one of content, name, ttl, proxied, priority, comment.")
    return {"updated": _record(_result("PATCH", f"/zones/{z['id']}/dns_records/{record_id}", body=body))}


def delete_record(zone, record_id):
    z = _zone(zone)
    old = _result("GET", f"/zones/{z['id']}/dns_records/{record_id}")
    _result("DELETE", f"/zones/{z['id']}/dns_records/{record_id}")
    return {"deleted": _record(old)}


def export_records(zone):
    z = _zone(zone)
    text = _call("GET", f"/zones/{z['id']}/dns_records/export", raw=True)
    return {"zone": z["name"], "bind_file": text[:6000], "truncated": len(text) > 6000}


def import_records(zone, bind_text):
    z = _zone(zone)
    boundary = "hiveboundary7f3a"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"zone.txt\"\r\n"
            f"Content-Type: text/plain\r\n\r\n{bind_text}\r\n--{boundary}--\r\n").encode()
    req = urllib.request.Request(f"{API}/zones/{z['id']}/dns_records/import", data=body, method="POST", headers={
        "Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}",
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            res = json.load(resp).get("result") or {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Cloudflare said {e.code}: {e.read().decode(errors='replace')[:300]}")
    return {"zone": z["name"], "added": res.get("recs_added"), "parsed": res.get("total_records_parsed")}


# --- DNSSEC -------------------------------------------------------------------

def get_dnssec(zone):
    z = _zone(zone)
    d = _result("GET", f"/zones/{z['id']}/dnssec")
    return {"zone": z["name"], "status": d.get("status"), "ds": d.get("ds"), "algorithm": d.get("algorithm"),
            "digest": d.get("digest"), "key_tag": d.get("key_tag")}


def set_dnssec(zone, enabled):
    z = _zone(zone)
    d = _result("PATCH", f"/zones/{z['id']}/dnssec", body={"status": "active" if enabled else "disabled"})
    out = {"zone": z["name"], "status": d.get("status")}
    if enabled:
        out["next_step"] = "Add this DS record at the registrar: " + str(d.get("ds"))
    return out


# --- OpenAI function-calling schemas -----------------------------------------

def _tool(name, description, props, required):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": props, "required": required}}}


ZONE = {"type": "string", "description": "The domain name, e.g. 'example.com'."}
RECORD_ID = {"type": "string", "description": "The record's id, from list_records."}
FIELDS = {
    "name": {"type": "string", "description": "Host name: 'www', 'mail.example.com' or '@' for the root."},
    "content": {"type": "string", "description": "The value: an IP for A/AAAA, a hostname for CNAME/MX/NS, the text for TXT."},
    "ttl": {"type": "integer", "description": "Seconds (30-86400), or 1 for automatic (default)."},
    "proxied": {"type": "boolean", "description": "Route through Cloudflare's proxy (orange cloud). Only A, AAAA and CNAME."},
    "priority": {"type": "integer", "description": "Priority for MX and SRV records, e.g. 10."},
    "comment": {"type": "string", "description": "A note shown beside the record."},
}

TOOLS = [
    _tool("list_zones", "List the domains (zones) in the Cloudflare account, with status and nameservers.",
          {"search": {"type": "string", "description": "Only domains containing this text."}}, []),
    _tool("get_zone", "Get one domain's status, plan and nameservers.", {"zone": ZONE}, ["zone"]),
    _tool("create_zone", "Add a new domain to the Cloudflare account. Returns the nameservers to set at the registrar.",
          {"domain": {"type": "string", "description": "Domain to add, e.g. 'example.com'."}}, ["domain"]),
    _tool("list_records", "List a domain's DNS records, optionally filtered by type, host name or text. Always use this to find a record's id before changing or deleting it.",
          {"zone": ZONE,
           "type": {"type": "string", "enum": RECORD_TYPES, "description": "Only this record type."},
           "name": {"type": "string", "description": "Only this host name, e.g. 'www' or '@'."},
           "search": {"type": "string", "description": "Only records whose name, content or comment contains this."}},
          ["zone"]),
    _tool("create_record", "Add a DNS record to a domain.",
          {"zone": ZONE, "type": {"type": "string", "enum": RECORD_TYPES, "description": "Record type."},
           **FIELDS}, ["zone", "type", "name", "content"]),
    _tool("update_record", "Change an existing DNS record. Only the fields you give are changed.",
          {"zone": ZONE, "record_id": RECORD_ID,
           "type": {"type": "string", "enum": RECORD_TYPES, "description": "New record type (rarely needed)."},
           **FIELDS}, ["zone", "record_id"]),
    _tool("delete_record", "Permanently delete one DNS record by id.",
          {"zone": ZONE, "record_id": RECORD_ID}, ["zone", "record_id"]),
    _tool("export_records", "Export all of a domain's DNS records as a BIND zone file (a backup).",
          {"zone": ZONE}, ["zone"]),
    _tool("import_records", "Import DNS records into a domain from BIND zone file text.",
          {"zone": ZONE, "bind_text": {"type": "string", "description": "The zone file contents."}},
          ["zone", "bind_text"]),
    _tool("get_dnssec", "Show a domain's DNSSEC status and DS record details.", {"zone": ZONE}, ["zone"]),
    _tool("set_dnssec", "Turn DNSSEC on or off for a domain. Turning it on returns the DS record to add at the registrar.",
          {"zone": ZONE, "enabled": {"type": "boolean", "description": "true to enable, false to disable."}},
          ["zone", "enabled"]),
]

HANDLERS = {
    "list_zones": list_zones,
    "get_zone": get_zone,
    "create_zone": create_zone,
    "list_records": list_records,
    "create_record": create_record,
    "update_record": update_record,
    "delete_record": delete_record,
    "export_records": export_records,
    "import_records": import_records,
    "get_dnssec": get_dnssec,
    "set_dnssec": set_dnssec,
}


# --- how results are shown in the browser (core/views.py) --------------------

VIEWS = {
    "list_zones": {"type": "table", "title": "Domains", "rows": "zones", "columns": [
        {"field": "name", "label": "Domain"},
        {"field": "status", "label": "Status"},
        {"field": "plan", "label": "Plan"},
    ]},
    "list_records": {"type": "table", "title": "DNS records for {zone}", "rows": "records", "columns": [
        {"field": "type", "label": "Type"},
        {"field": "name", "label": "Name"},
        {"field": "content", "label": "Content"},
        {"field": "ttl", "label": "TTL", "align": "right"},
        {"field": "proxied", "label": "Proxied"},
        {"field": "id", "label": "ID"},
    ]},
}
