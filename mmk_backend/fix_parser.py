"""FIX tag=value parsing (shared by WebSocket PM handler)."""


def parse_fix(fix_str: str) -> dict:
    """
    Parse a pipe-delimited FIX tag=value string.
    Key tags used in order handling:
      37  = EXCH_ORDER_ID
      41  = HOUSE_ORDER_ID
      11  = client order id (ordHash alias)
      54  = numeric side
      55  = symbol
      39  = order status
    """
    result: dict = {}
    for part in fix_str.split("|"):
        if "=" in part:
            tag, _, val = part.partition("=")
            result[tag.strip()] = val.strip()
    return result
