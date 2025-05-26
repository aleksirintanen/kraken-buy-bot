import os
import ccxt
import asyncio

# Configuration
DRY_RUN = os.getenv('DRY_RUN', 'False').lower() == 'true'

# Kraken API credentials
api_key = os.getenv('KRAKEN_API_KEY')
api_secret = os.getenv('KRAKEN_API_SECRET')

if not api_key or not api_secret:
    raise ValueError("KRAKEN_API_KEY and KRAKEN_API_SECRET environment variables must be set")

# Initialize Kraken exchange
kraken = ccxt.kraken({
    'apiKey': api_key,
    'secret': api_secret,
})

# Global event loop
_loop = None

def get_event_loop():
    """Get or create the event loop"""
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop 