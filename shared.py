import os
import ccxt

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