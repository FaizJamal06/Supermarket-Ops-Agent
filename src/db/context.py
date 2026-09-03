"""
Conversation context management.

Handles storing and retrieving conversation turns, with strict truncation
to avoid context window bloat (PRD §4.1 step 2).

MAX_HISTORY_TURNS = 10: only the last 10 turns are loaded into the LLM context.
The full history stays in the DB for audit, but the agent only sees recent turns.
"""

import json
from datetime import datetime, timezone
from src.db.connection import get_connection

# Strict limit to prevent context window bloat and latency spikes.
# 10 turns ≈ 5 user messages + 5 assistant responses — enough for multi-turn
# billing without risking rate limits on Claude.
MAX_HISTORY_TURNS = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_turn(chat_id: str, role: str, content) -> None:
    """
    Save a conversation turn to the DB.

    Args:
        chat_id: Telegram chat ID
        role: 'user' | 'assistant'
        content: string or dict/list (will be JSON-serialized if not string)
    """
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)

    conn = get_connection()
    conn.execute(
        "INSERT INTO conversation_turns (chat_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?)",
        (chat_id, role, content, _now()),
    )
    conn.commit()


def load_recent_turns(chat_id: str, limit: int = MAX_HISTORY_TURNS) -> list[dict]:
    """
    Load the most recent turns for a chat, strictly capped at `limit`.

    Returns messages in chronological order (oldest first), ready to be
    passed as the `messages` array to the Anthropic API.

    Only loads the last `limit` turns to prevent context window bloat.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT role, content FROM conversation_turns "
        "WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
        (chat_id, limit),
    ).fetchall()

    # Reverse to chronological order
    messages = []
    for row in reversed(rows):
        content = row["content"]
        # Try to parse JSON content (for tool_use/tool_result blocks)
        try:
            parsed = json.loads(content)
            if isinstance(parsed, (list, dict)):
                content = parsed
        except (json.JSONDecodeError, TypeError):
            pass

        messages.append({"role": row["role"], "content": content})

    return messages


def clear_history(chat_id: str) -> None:
    """
    Clear conversation history for a chat (e.g., on /new command).
    Only clears the conversation_turns table — business state (stock, bills,
    preferences) is untouched.
    """
    conn = get_connection()
    conn.execute("DELETE FROM conversation_turns WHERE chat_id = ?", (chat_id,))
    conn.commit()
