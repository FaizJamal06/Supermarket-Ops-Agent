"""
Telegram Bot entry point — PRD §4.1 (Telegram Webhook / Polling).

Uses long-polling for local testing and includes the Layer 1 Idempotency check (update_id).
"""

import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from src.db.connection import get_connection, init_db
from src.agent.loop import process_message

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def _is_update_processed(update_id: int) -> bool:
    """PRD §4.4.3 Layer 1: Check if update_id is already in processed_updates."""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM processed_updates WHERE update_id = ?",
        (update_id,)
    ).fetchone()
    return bool(row)


def _mark_update_processed(update_id: int) -> None:
    """Mark update_id as processed."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO processed_updates (update_id, processed_at) VALUES (?, ?)",
        (update_id, now)
    )
    conn.commit()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main message handler."""
    if not update.message or not update.message.text:
        return

    update_id = update.update_id
    if _is_update_processed(update_id):
        logger.info(f"Skipping duplicate update_id {update_id}")
        return

    # In a real multi-tenant app, owner_id would be mapped from the user's auth.
    # For this assignment, we use the Telegram user ID as the owner_id.
    chat_id = str(update.message.chat_id)
    owner_id = str(update.message.from_user.id)
    text = update.message.text

    # Show typing indicator while Anthropic processes
    await context.bot.send_chat_action(chat_id=chat_id, action='typing')

    try:
        # Run the Anthropic loop
        reply_text = process_message(owner_id, chat_id, text)
        
        # Send reply
        await context.bot.send_message(chat_id=chat_id, text=reply_text)
        
        # Mark processed
        _mark_update_processed(update_id)
        
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id, 
            text="Sorry, I encountered an internal error processing that request."
        )


def start_bot():
    """Initialize DB and start the Telegram bot."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token or token == "your-telegram-bot-token-here":
        logger.error("TELEGRAM_BOT_TOKEN not set in environment.")
        return

    init_db()
    
    app = ApplicationBuilder().token(token).build()
    
    # Handle all text messages
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    # Also handle the /new command specifically
    app.add_handler(MessageHandler(filters.Regex(r"^/new"), handle_message))

    logger.info("Starting Supermarket Ops Agent...")
    app.run_polling()


if __name__ == '__main__':
    start_bot()
