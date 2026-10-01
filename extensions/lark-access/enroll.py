"""Write a colleague into the official recognized list.

Uses the pairing store the gateway already checks. An empty allowlist stays empty;
only an allowlist that already exists is updated, which is the store's own rule.
"""

from __future__ import annotations


def record_recognized(user_id: str, user_name: str = "", platform: str = "feishu") -> bool:
    """Return True when this person was not on the list and is now."""
    from gateway.pairing import PairingStore, _matching_ids

    store = PairingStore()
    with store._lock:
        approved = store._load_json(store._approved_path(platform))
        if _matching_ids(platform, approved, user_id):
            return False
        store._approve_user(platform, user_id, user_name or "")
        return True
