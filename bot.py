import ccxt
import schedule
import time
import os
import json
from datetime import datetime

# Configuration
DRY_RUN = True  # Set to False to enable real trading
TEST_MODE = False  # Set to True for one-time real test purchase with minimum amount
STATE_FILE = 'bot_state.json'

def load_state():
    """Load the bot state from file"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        log_action(f"Error loading state: {e}")
    return {'monday_attempt_successful': False}

def save_state(state):
    """Save the bot state to file"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        log_action(f"Error saving state: {e}")

# Load initial state
state = load_state()
monday_attempt_successful = state['monday_attempt_successful']

# Kraken API credentials
api_key = os.getenv('KRAKEN_API_KEY')
api_secret = os.getenv('KRAKEN_API_SECRET')

# Initialize Kraken exchange
kraken = ccxt.kraken({
    'apiKey': api_key,
    'secret': api_secret,
})

# Define the trading pair and minimum BTC threshold
symbol = 'BTC/EUR'
min_btc_amount = 0.00005  # Minimum BTC amount to buy

def log_action(message):
    """Helper function to log actions with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode = "[DRY RUN]" if DRY_RUN else "[LIVE]"
    if TEST_MODE:
        mode = "[TEST MODE]"
    print(f"{timestamp} {mode} {message}")

def place_limit_order():
    global monday_attempt_successful
    try:
        # Fetch balance
        balance = kraken.fetch_balance()
        eur_balance = balance['total'].get('EUR', 0)
        log_action(f"Current EUR balance: {eur_balance:.2f}")

        # Use 20% of EUR balance or minimum amount in test mode
        if TEST_MODE:
            btc_amount = min_btc_amount  # Use exactly minimum amount in test mode
            log_action(f"TEST MODE: Will buy exactly {btc_amount:.8f} BTC")
        else:
            eur_to_use = eur_balance * 0.20
            log_action(f"Planning to use {eur_to_use:.2f} EUR (20% of balance)")

        for attempt in range(10):
            # Fetch order book and get level 3 bid price
            order_book = kraken.fetch_order_book(symbol)
            if len(order_book['bids']) < 3:
                log_action("Not enough bid levels in the order book.")
                return
            bid_price = order_book['bids'][2][0]
            log_action(f"Current level 3 bid price: {bid_price:.2f} EUR")

            # Calculate BTC amount to buy (only in non-test mode)
            if not TEST_MODE:
                btc_amount = eur_to_use / bid_price
                log_action(f"Would buy {btc_amount:.8f} BTC")

            # Check if BTC amount is above minimum (only in non-test mode)
            if not TEST_MODE and btc_amount < min_btc_amount:
                log_action(f"BTC amount {btc_amount:.8f} is below the minimum threshold of {min_btc_amount}. Skipping order.")
                return

            if DRY_RUN:
                # In dry run, check if current price is lower than our bid
                current_price = order_book['bids'][0][0]  # Get the best bid price
                if current_price <= bid_price:
                    log_action(f"SIMULATED: Current price {current_price:.2f} EUR is lower than our bid {bid_price:.2f} EUR")
                    log_action("SIMULATED: Order would be filled successfully")
                    monday_attempt_successful = True
                    save_state({'monday_attempt_successful': True})
                    return
                else:
                    log_action(f"SIMULATED: Current price {current_price:.2f} EUR is higher than our bid {bid_price:.2f} EUR")
                    log_action("SIMULATED: Order would not be filled")
                    time.sleep(5)  # Reduced sleep time for dry run
            else:
                # Place limit buy order
                order = kraken.create_limit_buy_order(symbol, btc_amount, bid_price)
                log_action(f"Limit buy order placed: {order}")

                # Wait for 5 minutes
                time.sleep(5 * 60)

                # Check order status
                order_status = kraken.fetch_order(order['id'])
                if order_status['status'] == 'closed':
                    log_action(f"Order {order['id']} filled successfully.")
                    monday_attempt_successful = True
                    save_state({'monday_attempt_successful': True})
                    if TEST_MODE:
                        log_action("Test purchase completed successfully. Exiting...")
                        exit(0)
                    return
                else:
                    kraken.cancel_order(order['id'])
                    log_action(f"Order {order['id']} not filled in 5 minutes. Cancelled. Retrying...")

        log_action("Max retries reached. Will try again next week.")

    except Exception as e:
        log_action(f"An error occurred: {e}")

def place_monday_order():
    """Primary order attempt on Monday"""
    global monday_attempt_successful
    monday_attempt_successful = False  # Reset the success flag
    save_state({'monday_attempt_successful': False})
    log_action("Starting Monday order attempt")
    place_limit_order()

def place_sunday_order():
    """Fallback order attempt on Sunday"""
    if not monday_attempt_successful:
        log_action("Monday attempt was not successful, running fallback attempt on Sunday")
        place_limit_order()
    else:
        log_action("Monday attempt was successful, skipping Sunday fallback")

# Handle different modes
if TEST_MODE:
    log_action("Starting in TEST MODE - will attempt one real purchase with minimum amount")
    log_action(f"Trading pair: {symbol}")
    log_action(f"Minimum BTC amount: {min_btc_amount}")
    place_limit_order()
elif DRY_RUN:
    log_action("Running in dry-run mode - executing once immediately")
    place_limit_order()
    log_action("Dry run completed - exiting")
    exit(0)
else:
    # Schedule primary attempt for Monday 2 AM UTC
    schedule.every().monday.at("02:00").do(place_monday_order)
    
    # Schedule fallback attempt for Sunday 2 AM UTC
    schedule.every().sunday.at("02:00").do(place_sunday_order)
    
    log_action("Bot started in LIVE mode")
    log_action(f"Trading pair: {symbol}")
    log_action(f"Minimum BTC amount: {min_btc_amount}")
    log_action("Scheduled to run on Monday 02:00 UTC with fallback to Sunday 02:00 UTC")
    log_action(f"Current state: Monday attempt {'successful' if monday_attempt_successful else 'not successful'}")

    # Keep the script running
    while True:
        schedule.run_pending()
        time.sleep(1)
