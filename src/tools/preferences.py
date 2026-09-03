"""
Preference tools — PRD §4.3 (Preference Tools).

Preferences are keyed on owner_id (NOT chat_id) — they persist across
Telegram chats (/new). This is the "memory" requirement from PRD §4.5.
"""

from datetime import datetime, timezone
from src.db.connection import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_preference(owner_id: str, key: str, value: str) -> dict:
    """
    Set a preference for an owner. Upserts (inserts or updates).

    Returns:
        {key, value, status: "saved"}
    """
    conn = get_connection()
    conn.execute(
        """INSERT INTO preferences (owner_id, key, value, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(owner_id, key) DO UPDATE SET value = ?, updated_at = ?""",
        (owner_id, key, value, _now(), value, _now()),
    )
    conn.commit()
    return {"key": key, "value": value, "status": "saved"}


def get_preference(owner_id: str, key: str) -> dict:
    """
    Get a single preference for an owner.

    Returns:
        {key, value} or {error: "not_set"}
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM preferences WHERE owner_id = ? AND key = ?",
        (owner_id, key),
    ).fetchone()
    if not row:
        return {"error": "not_set", "message": f"Preference '{key}' is not set."}
    return {"key": key, "value": row["value"]}


def get_all_preferences(owner_id: str) -> dict:
    """
    Get all preferences for an owner (bulk load for system prompt injection).

    Returns:
        {preferences: {key: value, ...}}
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT key, value FROM preferences WHERE owner_id = ?",
        (owner_id,),
    ).fetchall()
    return {"preferences": {r["key"]: r["value"] for r in rows}}
