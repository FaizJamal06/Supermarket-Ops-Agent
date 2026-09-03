"""
Supermarket Ops Agent — entry point.

Loads environment variables and starts the Telegram bot long-polling loop.
"""

from dotenv import load_dotenv
from src.telegram.bot import start_bot

if __name__ == "__main__":
    # Load secrets from .env file
    load_dotenv()
    
    # Start the bot
    start_bot()

