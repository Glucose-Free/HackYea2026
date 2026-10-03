import re

# Stub: Coder 2 replaces with Presidio + real policy data
RESTRICTED = {
    "falcon": {"type": "deal", "cleared_roles": ["banker", "compliance"]},
}


def detect(text):
    """Return a list of entities found in text."""
    found, seen = [], set()
    for word in RESTRICTED:
        for m in re.finditer(re.escape(word), text, re.IGNORECASE):
            key = m.group(0).lower()
            if key not in seen:
                seen.add(key)
                found.append({"text": m.group(0), "type": RESTRICTED[word]["type"], "key": word})
    return found


def decide(role, entities):
    """Return {"outcome": "allowed"|"blocked", "reason": str}."""
    for e in entities:
        if role not in RESTRICTED[e["key"]]["cleared_roles"]:
            return {"outcome": "blocked", "reason": "restricted entity referenced by unauthorized role"}
    return {
        "outcome": "allowed",
        "reason": "role cleared for entities" if entities else "no restricted entities",
    }