"""Tools for the money agent.

Each tool has two parts:
  - a schema in OpenAI function-calling format (listed in TOOLS)
  - a Python implementation (registered in HANDLERS, keyed by the same name)

Uses the free Frankfurter API (https://frankfurter.dev), which serves the
European Central Bank's reference rates - no API key required. The ECB
publishes once per working day, so the rate date can be a few days old.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

RATES_URL = "https://api.frankfurter.dev/v1/latest"

# Small models sometimes send a symbol or a name instead of the ISO code.
_ALIASES = {
    "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY",
    "DOLLAR": "USD", "DOLLARS": "USD", "EURO": "EUR", "EUROS": "EUR",
    "POUND": "GBP", "POUNDS": "GBP", "STERLING": "GBP", "YEN": "JPY",
}


def _code(value, label):
    code = str(value or "").strip().upper()
    code = _ALIASES.get(code, code)
    if len(code) != 3 or not code.isalpha():
        raise ValueError(
            f"Couldn't read the {label} currency '{value}'. "
            "Use a three-letter code such as 'USD', 'EUR' or 'GBP'."
        )
    return code


def convert(amount, to, **rest):
    """Convert an amount between two currencies at the latest published rate."""
    # "from" is a Python keyword, so it arrives via **rest.
    source = rest.get("from", rest.get("from_currency"))
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ValueError(f"Couldn't read the amount '{amount}'. Use a plain number such as 250.")
    if amount < 0:
        raise ValueError("The amount can't be negative.")
    src, dst = _code(source, "from"), _code(to, "to")

    query = urllib.parse.urlencode({"amount": amount, "base": src, "symbols": dst})
    try:
        req = urllib.request.Request(f"{RATES_URL}?{query}", headers={"User-Agent": "hive-penny/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as err:
        if err.code in (404, 422):
            raise ValueError(
                f"Frankfurter doesn't know '{src}' or '{dst}'. "
                "Use ISO codes such as 'USD', 'EUR', 'GBP', 'JPY'."
            )
        raise ValueError(f"The rates service answered with an error ({err.code}). Try again shortly.")
    except urllib.error.URLError as err:
        raise ValueError(f"Couldn't reach the rates service: {err.reason}.")

    result = data["rates"][dst]
    return {
        "amount": amount,
        "from": src,
        "to": dst,
        "result": result,
        "rate": result / amount if amount else None,
        "rate_date": data["date"],
        "source": "European Central Bank, via Frankfurter",
    }


# --- OpenAI function-calling schemas -----------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "convert",
            "description": "Convert an amount of money from one currency to another at the latest published rate, and return the date of that rate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "How much money to convert, e.g. 250.",
                    },
                    "from": {
                        "type": "string",
                        "description": "Three-letter code of the currency to convert from, e.g. 'USD'.",
                    },
                    "to": {
                        "type": "string",
                        "description": "Three-letter code of the currency to convert to, e.g. 'EUR'.",
                    },
                },
                "required": ["amount", "from", "to"],
            },
        },
    },
]

HANDLERS = {
    "convert": convert,
}
