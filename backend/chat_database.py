"""
chat_database.py — Async SQLite persistence for conversations and messages.
Uses a shared connection pool to avoid per-query connection churn.
"""

import aiosqlite
import asyncio
import os
import uuid
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "hive_chat.db")

# ── Connection Pool ────────────────────────────────────────────────

_pool_lock = asyncio.Lock()
_connection: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """Get the shared database connection, creating it if needed."""
    global _connection
    async with _pool_lock:
        if _connection is None:
            _connection = await aiosqlite.connect(DB_PATH)
            _connection.row_factory = aiosqlite.Row
            await _connection.execute("PRAGMA journal_mode=WAL")
            await _connection.execute("PRAGMA foreign_keys=ON")
        return _connection


async def close_db():
    """Close the shared database connection. Call on shutdown."""
    global _connection
    async with _pool_lock:
        if _connection is not None:
            await _connection.close()
            _connection = None


async def init_db():
    """Initialize database tables."""
    db = await get_db()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'New Chat',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_conversation
        ON messages(conversation_id, created_at);
    """)
    await db.commit()


# ── Conversation CRUD ──────────────────────────────────────────────

async def create_conversation(title: str = "New Chat") -> dict:
    """Create a new conversation and return it."""
    db = await get_db()
    conv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (conv_id, title, now, now),
    )
    await db.commit()
    return {"id": conv_id, "title": title, "created_at": now, "updated_at": now}


async def list_conversations() -> list[dict]:
    """List all conversations, newest first."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM conversations ORDER BY updated_at DESC"
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_conversation(conv_id: str) -> dict | None:
    """Get a single conversation by ID."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM conversations WHERE id = ?", (conv_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def update_conversation_title(conv_id: str, title: str):
    """Update a conversation's title."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
        (title, now, conv_id),
    )
    await db.commit()


async def delete_conversation(conv_id: str):
    """Delete a conversation and all its messages (CASCADE)."""
    db = await get_db()
    await db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    await db.commit()


# ── Message CRUD ───────────────────────────────────────────────────

async def add_message(conv_id: str, role: str, content: str) -> dict:
    """Add a message to a conversation."""
    db = await get_db()
    msg_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (msg_id, conv_id, role, content, now),
    )
    # Touch conversation updated_at
    await db.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id)
    )
    await db.commit()
    return {"id": msg_id, "conversation_id": conv_id, "role": role, "content": content, "created_at": now}


async def get_messages(conv_id: str) -> list[dict]:
    """Get all messages for a conversation, oldest first."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
        (conv_id,),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]
