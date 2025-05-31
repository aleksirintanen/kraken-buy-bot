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
from shared import kraken, DRY_RUN, get_event_loop  # Import from shared.py

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
STATE_FILE = 'bot_state.json'

# Global event loop
loop = None

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

async def get_available_balance(allow_usdc=True):
    """Get available balance in EUR or USDC
    
    Args:
        allow_usdc (bool): If True, USDC balance can be used as a fallback.
                          If False, only EUR balance will be considered.
    """
    balance = kraken.fetch_balance()
    eur_balance = balance['total'].get('EUR', 0)
    usdc_balance = balance['total'].get('USDC.F', 0)  # Use USDC.F for balance check
    
    if eur_balance >= 10:
        return 'EUR', eur_balance
    elif allow_usdc and usdc_balance >= 10:
        return 'USDC', usdc_balance  # Return 'USDC' for trading pair
    else:
        return None, 0

async def place_limit_order_btc():
    """Place a limit order to buy BTC"""
    retry_count = 0
    while retry_count < TRADING_CONFIG['max_retries']:
        try:
            metrics_manager.record_order_attempt()
            start_time = time.time()

            # Get current balance
            balance = kraken.fetch_balance()
            btc_balance = balance['total'].get('XBT.F', 0)
            
            # Get available balance in EUR or USDC
            currency, available_balance = await get_available_balance()
            if not currency:
                log_action("Insufficient EUR or USDC balance for trading (minimum 10 required)")
                return
            
            # Update metrics
            metrics_manager.update_balances(available_balance, btc_balance)
            
            # Log current balance
            log_action(f"Current {currency} balance: {available_balance:.2f}")
            log_action(f"Current BTC balance: {btc_balance:.8f}")
            
            # Calculate amount to use
            amount_to_use = available_balance * TRADING_CONFIG['balance_percentage']
            log_action(f"Planning to use {amount_to_use:.2f} {currency} ({TRADING_CONFIG['balance_percentage']*100}% of balance)")
            
            # Get current order book
            symbol = f"BTC/{currency}"
            order_book = kraken.fetch_order_book(symbol, limit=3)
            if not order_book or not order_book['bids']:
                log_action("No bids available in order book")
                return
                
            # Get the best bid price (highest price someone is willing to buy at)
            bid_price = order_book['bids'][0][0]
            log_action(f"Current level 3 bid price: {bid_price:.2f} {currency}")
            
            # Calculate BTC amount to buy
            btc_amount = amount_to_use / bid_price
            
            if btc_amount < TRADING_CONFIG['min_btc_amount']:
                log_action(f"Calculated BTC amount {btc_amount:.8f} is below minimum {TRADING_CONFIG['min_btc_amount']}")
                return
                
            # Place the order
            if DRY_RUN:
                current_price = order_book['bids'][0][0]
                if current_price <= bid_price:
                    log_action(
                        f"SIMULATED: Current price {current_price:.2f} {currency} is lower than our bid {bid_price:.2f} {currency}",
                        "SUCCESS"
                    )
                    metrics_manager.record_order_success(btc_amount, bid_price, time.time() - start_time)
                    return
                else:
                    log_action(
                        f"SIMULATED: Current price {current_price:.2f} {currency} is higher than our bid {bid_price:.2f} {currency}",
                        "WARNING"
                    )
                    metrics_manager.record_order_failure()
                    retry_count += 1
                    if retry_count < TRADING_CONFIG['max_retries']:
                        log_action(f"Retrying in {TRADING_CONFIG['retry_delay_seconds']} seconds... (Attempt {retry_count + 1}/{TRADING_CONFIG['max_retries']})")
                        await asyncio.sleep(TRADING_CONFIG['retry_delay_seconds'])
                    continue
            else:
                # Place limit buy order
                order = kraken.create_limit_buy_order(symbol, btc_amount, bid_price)
                log_action(f"Limit buy order placed: {order}")

                # Wait for order timeout
                await asyncio.sleep(TRADING_CONFIG['order_timeout_minutes'] * 60)

                # Check order status
                order_status = kraken.fetch_order(order['id'])
                if order_status['status'] == 'closed':
                    log_action(f"Order {order['id']} filled successfully.", "SUCCESS")
                    monday_attempt_successful = True
                    save_state({'monday_attempt_successful': True})
                    metrics_manager.record_order_success(btc_amount, bid_price, time.time() - start_time)
                    return
                else:
                    # Cancel the unfilled order
                    try:
                        kraken.cancel_order(order['id'])
                        log_action(f"Order {order['id']} not filled in {TRADING_CONFIG['order_timeout_minutes']} minutes. Cancelled.", "WARNING")
                    except Exception as e:
                        log_action(f"Error cancelling order {order['id']}: {e}", "WARNING")
                    
                    metrics_manager.record_order_failure()
                    retry_count += 1
                    
                    if retry_count < TRADING_CONFIG['max_retries']:
                        log_action(f"Retrying in {TRADING_CONFIG['retry_delay_seconds']} seconds... (Attempt {retry_count + 1}/{TRADING_CONFIG['max_retries']})")
                        await asyncio.sleep(TRADING_CONFIG['retry_delay_seconds'])
                        continue
                    else:
                        log_action(f"Maximum retry attempts ({TRADING_CONFIG['max_retries']}) reached. Giving up.", "WARNING")
                        return

        except Exception as e:
            log_action(f"An error occurred: {e}", "ERROR")
            metrics_manager.record_order_failure()
            retry_count += 1
            
            if retry_count < TRADING_CONFIG['max_retries']:
                log_action(f"Retrying in {TRADING_CONFIG['retry_delay_seconds']} seconds... (Attempt {retry_count + 1}/{TRADING_CONFIG['max_retries']})")
                await asyncio.sleep(TRADING_CONFIG['retry_delay_seconds'])
                continue
            else:
                log_action(f"Maximum retry attempts ({TRADING_CONFIG['max_retries']}) reached. Giving up.", "WARNING")
                return

    log_action(f"Failed to place order after {TRADING_CONFIG['max_retries']} attempts", "ERROR")

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
    run_async(place_limit_order_btc())

def place_sunday_order():
    """Fallback order attempt on Sunday"""
    if not monday_attempt_successful:
        log_action("Monday attempt was not successful, running fallback attempt on Sunday")
        run_async(place_limit_order_btc())
    else:
        log_action("Monday attempt was successful, skipping Sunday fallback")

async def place_limit_order_sol():
    """Place a limit order to buy SOL"""
    min_amount = 0.02  # Set your minimum SOL amount
    retry_count = 0
    while retry_count < TRADING_CONFIG['max_retries']:
        try:
            metrics_manager.record_order_attempt()
            start_time = time.time()
            
            # Get current balance
            balance = kraken.fetch_balance()
            sol_balance = balance['total'].get('SOL', 0)
            
            # Get available balance in EUR or USDC
            currency, available_balance = await get_available_balance()
            if not currency:
                log_action("Insufficient EUR or USDC balance for trading (minimum 10 required)")
                return
            
            # Update metrics
            metrics_manager.update_balances(available_balance, sol_balance)
            
            # Log current balance
            log_action(f"Current {currency} balance: {available_balance:.2f}")
            log_action(f"Current SOL balance: {sol_balance:.4f}")
            
            # Calculate amount to use
            amount_to_use = available_balance * TRADING_CONFIG['balance_percentage']
            log_action(f"Planning to use {amount_to_use:.2f} {currency} ({TRADING_CONFIG['balance_percentage']*100}% of balance)")
            
            # Get current order book
            symbol = f"SOL/{currency}"
            order_book = kraken.fetch_order_book(symbol, limit=3)
            if not order_book or not order_book['bids']:
                log_action("No bids available in order book")
                return
                
            # Get the best bid price
            bid_price = order_book['bids'][0][0]
            log_action(f"Current level 3 bid price: {bid_price:.2f} {currency}")
            
            # Calculate SOL amount to buy
            sol_amount = amount_to_use / bid_price
            
            if sol_amount < min_amount:
                log_action(f"Calculated SOL amount {sol_amount:.4f} is below minimum {min_amount}")
                return
                
            # Place the order
            if DRY_RUN:
                current_price = order_book['bids'][0][0]
                if current_price <= bid_price:
                    log_action(f"SIMULATED: Current price {current_price:.2f} {currency} is lower than our bid {bid_price:.2f} {currency}", "SUCCESS")
                    metrics_manager.record_order_success(sol_amount, bid_price, time.time() - start_time)
                    return
                else:
                    log_action(f"SIMULATED: Current price {current_price:.2f} {currency} is higher than our bid {bid_price:.2f} {currency}", "WARNING")
                    metrics_manager.record_order_failure()
                    retry_count += 1
                    if retry_count < TRADING_CONFIG['max_retries']:
                        log_action(f"Retrying in {TRADING_CONFIG['retry_delay_seconds']} seconds... (Attempt {retry_count + 1}/{TRADING_CONFIG['max_retries']})")
                        await asyncio.sleep(TRADING_CONFIG['retry_delay_seconds'])
                    continue
            else:
                order = kraken.create_limit_buy_order(symbol, sol_amount, bid_price)
                log_action(f"Limit buy order placed: {order}")

                # Wait for order timeout
                await asyncio.sleep(TRADING_CONFIG['order_timeout_minutes'] * 60)

                # Check order status
                order_status = kraken.fetch_order(order['id'])
                if order_status['status'] == 'closed':
                    log_action(f"Order {order['id']} filled successfully.", "SUCCESS")
                    metrics_manager.record_order_success(sol_amount, bid_price, time.time() - start_time)
                    return
                else:
                    try:
                        kraken.cancel_order(order['id'])
                        log_action(f"Order {order['id']} not filled in {TRADING_CONFIG['order_timeout_minutes']} minutes. Cancelled.", "WARNING")
                    except Exception as e:
                        log_action(f"Error cancelling order {order['id']}: {e}", "WARNING")
                    metrics_manager.record_order_failure()
                    retry_count += 1
                    if retry_count < TRADING_CONFIG['max_retries']:
                        log_action(f"Retrying in {TRADING_CONFIG['retry_delay_seconds']} seconds... (Attempt {retry_count + 1}/{TRADING_CONFIG['max_retries']})")
                        await asyncio.sleep(TRADING_CONFIG['retry_delay_seconds'])
                        continue
                    else:
                        log_action(f"Maximum retry attempts ({TRADING_CONFIG['max_retries']}) reached. Giving up.", "WARNING")
                        return
        except Exception as e:
            log_action(f"An error occurred: {e}", "ERROR")
            metrics_manager.record_order_failure()
            retry_count += 1
            if retry_count < TRADING_CONFIG['max_retries']:
                log_action(f"Retrying in {TRADING_CONFIG['retry_delay_seconds']} seconds... (Attempt {retry_count + 1}/{TRADING_CONFIG['max_retries']})")
                await asyncio.sleep(TRADING_CONFIG['retry_delay_seconds'])
                continue
            else:
                log_action(f"Maximum retry attempts ({TRADING_CONFIG['max_retries']}) reached. Giving up.", "WARNING")
                return
    log_action(f"Failed to place SOL order after {TRADING_CONFIG['max_retries']} attempts", "ERROR")

async def place_limit_order_eth():
    """Place a limit order to buy ETH"""
    min_amount = 0.002  # Set your minimum ETH amount
    retry_count = 0
    while retry_count < TRADING_CONFIG['max_retries']:
        try:
            metrics_manager.record_order_attempt()
            start_time = time.time()
            
            # Get current balance
            balance = kraken.fetch_balance()
            eth_balance = balance['total'].get('ETH', 0)
            
            # Get available balance in EUR or USDC
            currency, available_balance = await get_available_balance()
            if not currency:
                log_action("Insufficient EUR or USDC balance for trading (minimum 10 required)")
                return
            
            # Update metrics
            metrics_manager.update_balances(available_balance, eth_balance)
            
            # Log current balance
            log_action(f"Current {currency} balance: {available_balance:.2f}")
            log_action(f"Current ETH balance: {eth_balance:.4f}")
            
            # Calculate amount to use
            amount_to_use = available_balance * TRADING_CONFIG['balance_percentage']
            log_action(f"Planning to use {amount_to_use:.2f} {currency} ({TRADING_CONFIG['balance_percentage']*100}% of balance)")
            
            # Get current order book
            symbol = f"ETH/{currency}"
            order_book = kraken.fetch_order_book(symbol, limit=3)
            if not order_book or not order_book['bids']:
                log_action("No bids available in order book")
                return
                
            # Get the best bid price
            bid_price = order_book['bids'][0][0]
            log_action(f"Current level 3 bid price: {bid_price:.2f} {currency}")
            
            # Calculate ETH amount to buy
            eth_amount = amount_to_use / bid_price
            
            if eth_amount < min_amount:
                log_action(f"Calculated ETH amount {eth_amount:.4f} is below minimum {min_amount}")
                return
                
            # Place the order
            if DRY_RUN:
                current_price = order_book['bids'][0][0]
                if current_price <= bid_price:
                    log_action(f"SIMULATED: Current price {current_price:.2f} {currency} is lower than our bid {bid_price:.2f} {currency}", "SUCCESS")
                    metrics_manager.record_order_success(eth_amount, bid_price, time.time() - start_time)
                    return
                else:
                    log_action(f"SIMULATED: Current price {current_price:.2f} {currency} is higher than our bid {bid_price:.2f} {currency}", "WARNING")
                    metrics_manager.record_order_failure()
                    retry_count += 1
                    if retry_count < TRADING_CONFIG['max_retries']:
                        log_action(f"Retrying in {TRADING_CONFIG['retry_delay_seconds']} seconds... (Attempt {retry_count + 1}/{TRADING_CONFIG['max_retries']})")
                        await asyncio.sleep(TRADING_CONFIG['retry_delay_seconds'])
                    continue
            else:
                order = kraken.create_limit_buy_order(symbol, eth_amount, bid_price)
                log_action(f"Limit buy order placed: {order}")

                # Wait for order timeout
                await asyncio.sleep(TRADING_CONFIG['order_timeout_minutes'] * 60)

                # Check order status
                order_status = kraken.fetch_order(order['id'])
                if order_status['status'] == 'closed':
                    log_action(f"Order {order['id']} filled successfully.", "SUCCESS")
                    metrics_manager.record_order_success(eth_amount, bid_price, time.time() - start_time)
                    return
                else:
                    try:
                        kraken.cancel_order(order['id'])
                        log_action(f"Order {order['id']} not filled in {TRADING_CONFIG['order_timeout_minutes']} minutes. Cancelled.", "WARNING")
                    except Exception as e:
                        log_action(f"Error cancelling order {order['id']}: {e}", "WARNING")
                    metrics_manager.record_order_failure()
                    retry_count += 1
                    if retry_count < TRADING_CONFIG['max_retries']:
                        log_action(f"Retrying in {TRADING_CONFIG['retry_delay_seconds']} seconds... (Attempt {retry_count + 1}/{TRADING_CONFIG['max_retries']})")
                        await asyncio.sleep(TRADING_CONFIG['retry_delay_seconds'])
                        continue
                    else:
                        log_action(f"Maximum retry attempts ({TRADING_CONFIG['max_retries']}) reached. Giving up.", "WARNING")
                        return
        except Exception as e:
            log_action(f"An error occurred: {e}", "ERROR")
            metrics_manager.record_order_failure()
            retry_count += 1
            if retry_count < TRADING_CONFIG['max_retries']:
                log_action(f"Retrying in {TRADING_CONFIG['retry_delay_seconds']} seconds... (Attempt {retry_count + 1}/{TRADING_CONFIG['max_retries']})")
                await asyncio.sleep(TRADING_CONFIG['retry_delay_seconds'])
                continue
            else:
                log_action(f"Maximum retry attempts ({TRADING_CONFIG['max_retries']}) reached. Giving up.", "WARNING")
                return
    log_action(f"Failed to place ETH order after {TRADING_CONFIG['max_retries']} attempts", "ERROR")

async def place_limit_order_usdc():
    """Place a limit order to buy USDC"""
    min_amount = 5  # Set your minimum USDC amount
    retry_count = 0
    while retry_count < TRADING_CONFIG['max_retries']:
        try:
            metrics_manager.record_order_attempt()
            start_time = time.time()
            
            # Get current balance
            balance = kraken.fetch_balance()
            usdc_balance = balance['total'].get('USDC.F', 0)  # Use USDC.F for balance check
            
            # Get available balance in EUR
            currency, available_balance = await get_available_balance()
            if not currency or currency != 'EUR':  # USDC can only be bought with EUR
                log_action("Insufficient EUR balance for trading USDC (minimum 10 required)")
                return
            
            # Update metrics
            metrics_manager.update_balances(available_balance, usdc_balance)
            
            # Log current balance
            log_action(f"Current EUR balance: {available_balance:.2f}")
            log_action(f"Current USDC balance: {usdc_balance:.2f}")
            
            # Calculate amount to use
            amount_to_use = available_balance * TRADING_CONFIG['balance_percentage']
            log_action(f"Planning to use {amount_to_use:.2f} EUR ({TRADING_CONFIG['balance_percentage']*100}% of balance)")
            
            # Get current order book
            symbol = 'USDC/EUR'  # USDC can only be bought with EUR
            order_book = kraken.fetch_order_book(symbol, limit=3)
            if not order_book or not order_book['bids']:
                log_action("No bids available in order book")
                return
                
            # Get the best bid price
            bid_price = order_book['bids'][0][0]
            log_action(f"Current level 3 bid price: {bid_price:.4f} EUR")
            
            # Calculate USDC amount to buy
            usdc_amount = amount_to_use / bid_price
            
            if usdc_amount < min_amount:
                log_action(f"Calculated USDC amount {usdc_amount:.2f} is below minimum {min_amount}")
                return
                
            # Place the order
            if DRY_RUN:
                current_price = order_book['bids'][0][0]
                if current_price <= bid_price:
                    log_action(f"SIMULATED: Current price {current_price:.4f} EUR is lower than our bid {bid_price:.4f} EUR", "SUCCESS")
                    metrics_manager.record_order_success(usdc_amount, bid_price, time.time() - start_time)
                    return
                else:
                    log_action(f"SIMULATED: Current price {current_price:.4f} EUR is higher than our bid {bid_price:.4f} EUR", "WARNING")
                    metrics_manager.record_order_failure()
                    retry_count += 1
                    if retry_count < TRADING_CONFIG['max_retries']:
                        log_action(f"Retrying in {TRADING_CONFIG['retry_delay_seconds']} seconds... (Attempt {retry_count + 1}/{TRADING_CONFIG['max_retries']})")
                        await asyncio.sleep(TRADING_CONFIG['retry_delay_seconds'])
                    continue
            else:
                order = kraken.create_limit_buy_order(symbol, usdc_amount, bid_price)
                log_action(f"Limit buy order placed: {order}")

                # Wait for order timeout
                await asyncio.sleep(TRADING_CONFIG['order_timeout_minutes'] * 60)

                # Check order status
                order_status = kraken.fetch_order(order['id'])
                if order_status['status'] == 'closed':
                    log_action(f"Order {order['id']} filled successfully.", "SUCCESS")
                    metrics_manager.record_order_success(usdc_amount, bid_price, time.time() - start_time)
                    return
                else:
                    try:
                        kraken.cancel_order(order['id'])
                        log_action(f"Order {order['id']} not filled in {TRADING_CONFIG['order_timeout_minutes']} minutes. Cancelled.", "WARNING")
                    except Exception as e:
                        log_action(f"Error cancelling order {order['id']}: {e}", "WARNING")
                    metrics_manager.record_order_failure()
                    retry_count += 1
                    if retry_count < TRADING_CONFIG['max_retries']:
                        log_action(f"Retrying in {TRADING_CONFIG['retry_delay_seconds']} seconds... (Attempt {retry_count + 1}/{TRADING_CONFIG['max_retries']})")
                        await asyncio.sleep(TRADING_CONFIG['retry_delay_seconds'])
                        continue
                    else:
                        log_action(f"Maximum retry attempts ({TRADING_CONFIG['max_retries']}) reached. Giving up.", "WARNING")
                        return
        except Exception as e:
            log_action(f"An error occurred: {e}", "ERROR")
            metrics_manager.record_order_failure()
            retry_count += 1
            if retry_count < TRADING_CONFIG['max_retries']:
                log_action(f"Retrying in {TRADING_CONFIG['retry_delay_seconds']} seconds... (Attempt {retry_count + 1}/{TRADING_CONFIG['max_retries']})")
                await asyncio.sleep(TRADING_CONFIG['retry_delay_seconds'])
                continue
            else:
                log_action(f"Maximum retry attempts ({TRADING_CONFIG['max_retries']}) reached. Giving up.", "WARNING")
                return
    log_action(f"Failed to place USDC order after {TRADING_CONFIG['max_retries']} attempts", "ERROR")

def initialize_bot():
    """Initialize the bot and set up callbacks"""
    # Set up the buy callbacks in the notification manager
    notification_manager.set_buy_callback(place_limit_order_btc)
    notification_manager.set_buy_sol_callback(place_limit_order_sol)
    notification_manager.set_buy_eth_callback(place_limit_order_eth)
    notification_manager.set_buy_usdc_callback(place_limit_order_usdc)
    
    # Set up scheduling state callback
    def handle_scheduling_state(enabled):
        """Handle scheduling state changes"""
        if enabled:
            # Re-enable all scheduled tasks
            schedule.every().monday.at(SCHEDULE_CONFIG['monday_time']).do(place_monday_order)
            schedule.every().sunday.at(SCHEDULE_CONFIG['sunday_time']).do(place_sunday_order)
            log_action("Bot scheduling has been enabled", "SUCCESS")
        else:
            # Clear all scheduled tasks
            schedule.clear()
            log_action("Bot scheduling has been disabled", "WARNING")
    
    notification_manager.set_scheduling_state_callback(handle_scheduling_state)
    
    # Start the metrics server if enabled
    if metrics_manager.enabled:
        logger.info("Metrics server is enabled")
    
    # Load initial state
    state = load_state()
    logger.info(f"Bot initialized with state: {state}")

# Handle different modes
if DRY_RUN:
    log_action("Starting in DRY RUN mode - will simulate trading without placing real orders", "SUCCESS")
    log_action(f"Trading pair: BTC/EUR")
    log_action(f"Minimum BTC amount: {TRADING_CONFIG['min_btc_amount']}")
   
    # Schedule primary attempt for Monday
    schedule.every().monday.at(SCHEDULE_CONFIG['monday_time']).do(place_monday_order)
    
    # Schedule fallback attempt for Sunday
    schedule.every().sunday.at(SCHEDULE_CONFIG['sunday_time']).do(place_sunday_order)
    
    log_action(f"Scheduled to run on Monday {SCHEDULE_CONFIG['monday_time']} {SCHEDULE_CONFIG['timezone']} with fallback to Sunday {SCHEDULE_CONFIG['sunday_time']} {SCHEDULE_CONFIG['timezone']}")
    log_action(f"Current state: Monday attempt {'successful' if monday_attempt_successful else 'not successful'}")
else:
    # Schedule primary attempt for Monday
    schedule.every().monday.at(SCHEDULE_CONFIG['monday_time']).do(place_monday_order)
    
    # Schedule fallback attempt for Sunday
    schedule.every().sunday.at(SCHEDULE_CONFIG['sunday_time']).do(place_sunday_order)
    
    log_action("Bot started in LIVE mode", "SUCCESS")
    log_action(f"Trading pair: BTC/EUR")
    log_action(f"Minimum BTC amount: {TRADING_CONFIG['min_btc_amount']}")
    log_action(f"Scheduled to run on Monday {SCHEDULE_CONFIG['monday_time']} {SCHEDULE_CONFIG['timezone']} with fallback to Sunday {SCHEDULE_CONFIG['sunday_time']} {SCHEDULE_CONFIG['timezone']}")
    log_action(f"Current state: Monday attempt {'successful' if monday_attempt_successful else 'not successful'}")

if __name__ == "__main__":
    try:
        initialize_bot()
        # Main loop
        while True:
            # Only run scheduled tasks if scheduling is enabled
            if notification_manager.is_scheduling_enabled():
                schedule.run_pending()
            loop = get_event_loop()
            loop.run_until_complete(asyncio.sleep(1))
    except KeyboardInterrupt:
        log_action("Bot stopped by user", "WARNING")
    finally:
        if loop:
            loop.close()
