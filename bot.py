import ccxt
import schedule
import time
import os
import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from functools import partial

from config import TRADING_CONFIG, SCHEDULE_CONFIG
from notifications import notification_manager
from metrics import metrics_manager

# Create logs directory if it doesn't exist
Path("logs").mkdir(exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
DRY_RUN = os.getenv('DRY_RUN', 'False').lower() == 'true'
STATE_FILE = 'bot_state.json'

# Global event loop
loop = None

def get_event_loop():
    """Get or create the event loop"""
    global loop
    if loop is None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop

async def send_notification_async(message, level="INFO"):
    """Async wrapper for sending notifications"""
    try:
        await notification_manager.send_notification(message, level)
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")

def log_action(message, level="INFO"):
    """Helper function to log actions and send notifications"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode = "[DRY RUN]" if DRY_RUN else "[LIVE]"
    log_message = f"{timestamp} {mode} {message}"
    
    # Log to file and console
    if level == "ERROR":
        logger.error(log_message)
    elif level == "WARNING":
        logger.warning(log_message)
    else:
        logger.info(log_message)
    
    # Send notification if it's an important message
    if level in ["ERROR", "WARNING", "SUCCESS"]:
        try:
            logger.debug(f"Attempting to send {level} notification: {message}")
            loop = get_event_loop()
            loop.create_task(send_notification_async(message, level))
        except Exception as e:
            logger.error(f"Failed to schedule notification: {e}")

def load_state():
    """Load the bot state from file"""
    try:
        if os.path.exists(STATE_FILE) and not os.path.isdir(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        else:
            # Initialize state file if it doesn't exist or is a directory
            if os.path.isdir(STATE_FILE):
                os.rmdir(STATE_FILE)
            initial_state = {'monday_attempt_successful': False}
            save_state(initial_state)
            return initial_state
    except Exception as e:
        logger.error(f"Error loading state: {e}")
        return {'monday_attempt_successful': False}

def save_state(state):
    """Save the bot state to file"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        logger.error(f"Error saving state: {e}")

# Load initial state
state = load_state()
monday_attempt_successful = state['monday_attempt_successful']

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

async def place_limit_order():
    """Place a limit order to buy BTC"""
    try:
        metrics_manager.record_order_attempt()
        start_time = time.time()

        # Get current balance
        balance = kraken.fetch_balance()
        usdc_balance = balance['total'].get('USDC', 0)
        btc_balance = balance['total'].get('BTC', 0)
        
        # Update metrics
        metrics_manager.update_balances(usdc_balance, btc_balance)
        
        # Log current balance
        log_action(f"Current USDC balance: {usdc_balance:.2f}")
        log_action(f"Current BTC balance: {btc_balance:.8f}")
        
        if usdc_balance < 10:  # Minimum 10 USDC required
            log_action("Insufficient USDC balance for trading")
            return
        
        # Calculate amount to use
        usdc_to_use = usdc_balance * TRADING_CONFIG['balance_percentage']
        log_action(f"Planning to use {usdc_to_use:.2f} USDC ({TRADING_CONFIG['balance_percentage']*100}% of balance)")
        
        # Get current order book
        order_book = kraken.fetch_order_book(TRADING_CONFIG['symbol'], limit=3)
        if not order_book or not order_book['bids']:
            log_action("No bids available in order book")
            return
            
        # Get the best bid price (highest price someone is willing to buy at)
        bid_price = order_book['bids'][0][0]
        log_action(f"Current level 3 bid price: {bid_price:.2f} USDC")
        
        # Calculate BTC amount to buy
        btc_amount = usdc_to_use / bid_price
        
        if btc_amount < TRADING_CONFIG['min_btc_amount']:
            log_action(f"Calculated BTC amount {btc_amount:.8f} is below minimum {TRADING_CONFIG['min_btc_amount']}")
            return
            
        # Place the order
        if DRY_RUN:
            current_price = order_book['bids'][0][0]
            if current_price <= bid_price:
                log_action(
                    f"SIMULATED: Current price {current_price:.2f} USDC is lower than our bid {bid_price:.2f} USDC",
                    "SUCCESS"
                )
                metrics_manager.record_order_success(btc_amount, bid_price, time.time() - start_time)
            else:
                log_action(
                    f"SIMULATED: Current price {current_price:.2f} USDC is higher than our bid {bid_price:.2f} USDC",
                    "WARNING"
                )
                metrics_manager.record_order_failure()
        else:
            # Place limit buy order
            order = kraken.create_limit_buy_order(TRADING_CONFIG['symbol'], btc_amount, bid_price)
            log_action(f"Limit buy order placed: {order}")

            # Wait for order timeout
            time.sleep(TRADING_CONFIG['order_timeout_minutes'] * 60)

            # Check order status
            order_status = kraken.fetch_order(order['id'])
            if order_status['status'] == 'closed':
                log_action(f"Order {order['id']} filled successfully.", "SUCCESS")
                monday_attempt_successful = True
                save_state({'monday_attempt_successful': True})
                metrics_manager.record_order_success(btc_amount, bid_price, time.time() - start_time)
                return
            else:
                kraken.cancel_order(order['id'])
                log_action(f"Order {order['id']} not filled in {TRADING_CONFIG['order_timeout_minutes']} minutes. Cancelled. Retrying...", "WARNING")
                metrics_manager.record_order_failure()

    except Exception as e:
        log_action(f"An error occurred: {e}", "ERROR")
        metrics_manager.record_order_failure()

def run_async(coro):
    """Helper function to run coroutines in the event loop"""
    loop = get_event_loop()
    return loop.run_until_complete(coro)

def place_monday_order():
    """Primary order attempt on Monday"""
    global monday_attempt_successful
    monday_attempt_successful = False
    save_state({'monday_attempt_successful': False})
    log_action("Starting Monday order attempt")
    run_async(place_limit_order())

def place_sunday_order():
    """Fallback order attempt on Sunday"""
    if not monday_attempt_successful:
        log_action("Monday attempt was not successful, running fallback attempt on Sunday")
        run_async(place_limit_order())
    else:
        log_action("Monday attempt was successful, skipping Sunday fallback")

async def convert_eur_to_usdc():
    """Convert EUR balance to USDC if available"""
    try:
        # Get current balance
        balance = kraken.fetch_balance()
        eur_balance = balance['total'].get('EUR', 0)
        
        if eur_balance < 10:  # Minimum 10 EUR required for conversion
            logger.debug(f"Insufficient EUR balance for conversion: {eur_balance:.2f} EUR")
            return
            
        log_action(f"Found EUR balance: {eur_balance:.2f} EUR")
        
        # Get current EUR/USDC price
        try:
            ticker = kraken.fetch_ticker('USDC/EUR')
            eur_usdc_price = ticker.get('last', 0)
            if not eur_usdc_price:
                log_action("Could not fetch USDC/EUR price", "WARNING")
                return
                
            log_action(f"Current USDC/EUR price: {eur_usdc_price:.4f}")
            
            # Calculate USDC amount we would get
            usdc_amount = eur_balance * eur_usdc_price
            
            if DRY_RUN:
                log_action(
                    f"SIMULATED: Would convert {eur_balance:.2f} EUR to {usdc_amount:.2f} USDC",
                    "SUCCESS"
                )
                return
                
            # Place market sell order for EUR/USDC
            try:
                order = kraken.create_market_sell_order('USDC/EUR', eur_balance)
                log_action(
                    f"Successfully converted {eur_balance:.2f} EUR to {usdc_amount:.2f} USDC",
                    "SUCCESS"
                )
                # Send notification
                await send_notification_async(
                    f"✅ Converted {eur_balance:.2f} EUR to {usdc_amount:.2f} USDC\n"
                    f"Rate: 1 EUR = {eur_usdc_price:.4f} USDC",
                    "SUCCESS"
                )
            except Exception as e:
                log_action(f"Failed to convert EUR to USDC: {str(e)}", "ERROR")
                await send_notification_async(
                    f"❌ Failed to convert EUR to USDC: {str(e)}",
                    "ERROR"
                )
                
        except Exception as e:
            log_action(f"Error fetching USDC/EUR price: {str(e)}", "ERROR")
            
    except Exception as e:
        log_action(f"Error in EUR to USDC conversion: {str(e)}", "ERROR")

def check_eur_balance():
    """Wrapper function to run EUR to USDC conversion"""
    run_async(convert_eur_to_usdc())

# Handle different modes
if DRY_RUN:
    log_action("Starting in DRY RUN mode - will simulate trading without placing real orders", "SUCCESS")
    log_action(f"Trading pair: {TRADING_CONFIG['symbol']}")
    log_action(f"Minimum BTC amount: {TRADING_CONFIG['min_btc_amount']}")
    
    # Schedule primary attempt for Monday
    schedule.every().monday.at(SCHEDULE_CONFIG['monday_time']).do(place_monday_order)
    
    # Schedule fallback attempt for Sunday
    schedule.every().sunday.at(SCHEDULE_CONFIG['sunday_time']).do(place_sunday_order)
    
    # Schedule EUR to USDC conversion check every hour
    schedule.every().hour.do(check_eur_balance)
    
    log_action(f"Scheduled to run on Monday {SCHEDULE_CONFIG['monday_time']} {SCHEDULE_CONFIG['timezone']} with fallback to Sunday {SCHEDULE_CONFIG['sunday_time']} {SCHEDULE_CONFIG['timezone']}")
    log_action("EUR to USDC conversion check scheduled every hour")
    log_action(f"Current state: Monday attempt {'successful' if monday_attempt_successful else 'not successful'}")
else:
    # Schedule primary attempt for Monday
    schedule.every().monday.at(SCHEDULE_CONFIG['monday_time']).do(place_monday_order)
    
    # Schedule fallback attempt for Sunday
    schedule.every().sunday.at(SCHEDULE_CONFIG['sunday_time']).do(place_sunday_order)
    
    # Schedule EUR to USDC conversion check every hour
    schedule.every().hour.do(check_eur_balance)
    
    log_action("Bot started in LIVE mode", "SUCCESS")
    log_action(f"Trading pair: {TRADING_CONFIG['symbol']}")
    log_action(f"Minimum BTC amount: {TRADING_CONFIG['min_btc_amount']}")
    log_action(f"Scheduled to run on Monday {SCHEDULE_CONFIG['monday_time']} {SCHEDULE_CONFIG['timezone']} with fallback to Sunday {SCHEDULE_CONFIG['sunday_time']} {SCHEDULE_CONFIG['timezone']}")
    log_action("EUR to USDC conversion check scheduled every hour")
    log_action(f"Current state: Monday attempt {'successful' if monday_attempt_successful else 'not successful'}")

# Keep the script running
try:
    while True:
        schedule.run_pending()
        loop = get_event_loop()
        loop.run_until_complete(asyncio.sleep(1))
except KeyboardInterrupt:
    log_action("Bot stopped by user", "WARNING")
finally:
    if loop:
        loop.close()
