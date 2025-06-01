import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.error import TelegramError, BadRequest, NetworkError
from config import NOTIFICATION_CONFIG
import asyncio
import threading
from datetime import datetime
import time
import signal

logger = logging.getLogger(__name__)

class NotificationManager:
    def __init__(self):
        self.telegram_bot = None
        self.initialized = False
        self.updater = None
        self._polling_thread = None
        self._last_command_time = 0
        self._command_cooldown = 2  # seconds between commands
        self._stop_event = threading.Event()
        self._reconnect_delay = 5  # seconds to wait before reconnecting
        self._startup_notification_sent = False  # Track if startup notification was sent
        self._loop = None  # Will be set when needed
        self._pending_buy_confirmation = {}  # chat_id: (timestamp, command_type, amount, currency, is_percentage)
        self._buy_callback = None  # Callback for buy orders
        self._scheduling_enabled = True  # Track if bot scheduling is enabled
        self._scheduling_state_callback = None  # Callback to control bot scheduling
        self._buy_sol_callback = None  # Callback for SOL buy orders
        self._buy_eth_callback = None  # Callback for ETH buy orders
        self._buy_usdc_callback = None  # Callback for USDC buy orders
        self._last_price_check = {}  # Store last price check time per chat
        self._price_check_cooldown = 10  # seconds between price checks

    def _start_polling(self):
        """Start polling in a separate thread"""
        while not self._stop_event.is_set():
            try:
                if not self.initialized or self.updater is None:
                    logger.warning("Polling thread detected bot not initialized, attempting to initialize...")
                    asyncio.run(self.initialize())
                    if not self.initialized:
                        logger.error("Failed to initialize bot in polling thread")
                        time.sleep(self._reconnect_delay)
                        continue

                logger.info("Starting Telegram bot polling...")
                self.updater.start_polling(drop_pending_updates=True, poll_interval=0.5)
                logger.info("Telegram bot polling started successfully")
                
                # Keep the thread alive and monitor the polling
                while not self._stop_event.is_set():
                    if not self.updater.running:
                        logger.warning("Polling stopped, attempting to restart...")
                        break
                    time.sleep(1)
                
                if not self._stop_event.is_set():
                    logger.warning("Polling stopped, attempting to reinitialize...")
                    self.initialized = False
                    time.sleep(self._reconnect_delay)
                    
            except Exception as e:
                logger.error(f"Error in polling thread: {e}")
                if not self._stop_event.is_set():
                    time.sleep(self._reconnect_delay)
                    self.initialized = False

    def stop(self):
        """Stop the notification manager"""
        logger.info("Stopping notification manager...")
        self._stop_event.set()
        if self.updater:
            try:
                self.updater.stop()
            except Exception as e:
                logger.error(f"Error stopping updater: {e}")
        if self._polling_thread and self._polling_thread.is_alive():
            self._polling_thread.join(timeout=5)
        logger.info("Notification manager stopped")

    async def initialize(self):
        """Initialize the Telegram bot asynchronously"""
        if self.initialized and self.updater and self.updater.running:
            logger.info("Telegram bot already initialized and running")
            return

        if not NOTIFICATION_CONFIG['telegram_enabled']:
            logger.info("Telegram notifications are disabled")
            return

        if not NOTIFICATION_CONFIG['telegram_token']:
            logger.error("Telegram bot token is not set")
            return

        if not NOTIFICATION_CONFIG['telegram_chat_id']:
            logger.error("Telegram chat ID is not set")
            return

        try:
            logger.info("Initializing Telegram bot...")
            # Initialize the bot and updater
            if self.updater:
                try:
                    self.updater.stop()
                except Exception as e:
                    logger.error(f"Error stopping existing updater: {e}")
            
            self.updater = Updater(token=NOTIFICATION_CONFIG['telegram_token'], use_context=True)
            self.telegram_bot = self.updater.bot
            
            # Add command handlers
            dispatcher = self.updater.dispatcher
            dispatcher.add_handler(CommandHandler("start", self.handle_start_command))
            dispatcher.add_handler(CommandHandler("help", self.handle_help_command))
            dispatcher.add_handler(CommandHandler("buy", self.handle_buy_command))
            dispatcher.add_handler(CommandHandler("buysol", self.handle_buy_sol_command))
            dispatcher.add_handler(CommandHandler("buyeth", self.handle_buy_eth_command))
            dispatcher.add_handler(CommandHandler("buyusdc", self.handle_buy_usdc_command))
            dispatcher.add_handler(CommandHandler("status", self.handle_status_command))
            dispatcher.add_handler(CommandHandler("confirm", self.handle_confirm_command))
            dispatcher.add_handler(CommandHandler("enable", self.handle_enable_command))
            dispatcher.add_handler(CommandHandler("disable", self.handle_disable_command))
            dispatcher.add_handler(CommandHandler("price", self.handle_price_command))
            dispatcher.add_handler(CommandHandler("balance", self.handle_balance_command))
            dispatcher.add_handler(CommandHandler("history", self.handle_history_command))
            
            # Test the connection and verify chat
            logger.info("Testing Telegram bot connection...")
            bot_info = self.telegram_bot.get_me()
            logger.info(f"Telegram bot connection successful. Bot username: @{bot_info.username}")
            
            # Verify chat access and send startup notification if not sent yet
            try:
                startup_message = (
                    "🔔 Bot is starting up and testing notifications...\n\n"
                    "Available commands:\n"
                    "/start - Start the bot and get welcome message\n"
                    "/help - Show detailed help for all commands\n"
                    "/buy - Buy BTC with available EUR/USDC\n"
                    "/buysol - Buy SOL with available EUR/USDC\n"
                    "/buyeth - Buy ETH with available EUR/USDC\n"
                    "/buyusdc - Buy USDC with EUR\n"
                    "/price - Check current prices\n"
                    "/balance - Check your balances\n"
                    "/status - Check bot status and all balances\n"
                    "/history - View recent trading history\n"
                    "/enable - Enable bot scheduling\n"
                    "/disable - Disable bot scheduling"
                )
                
                if not self._startup_notification_sent:
                    self.telegram_bot.send_message(
                        chat_id=NOTIFICATION_CONFIG['telegram_chat_id'],
                        text=startup_message
                    )
                    self._startup_notification_sent = True
                    logger.info("Startup notification sent successfully")
                else:
                    logger.info("Startup notification already sent, skipping")
                
                logger.info("Successfully verified chat access")
                
                # Start polling in a separate thread if not already running
                if self._polling_thread is None or not self._polling_thread.is_alive():
                    self._polling_thread = threading.Thread(target=self._start_polling, daemon=True)
                    self._polling_thread.start()
                    logger.info("Started new polling thread")
                
                self.initialized = True
                logger.info("Telegram bot initialization completed successfully")
                
            except BadRequest as e:
                if "chat not found" in str(e).lower():
                    logger.error(
                        f"Chat not found. Please make sure:\n"
                        f"1. You have started a chat with @{bot_info.username}\n"
                        f"2. The chat ID {NOTIFICATION_CONFIG['telegram_chat_id']} is correct\n"
                        f"3. You have sent at least one message to the bot"
                    )
                else:
                    logger.error(f"Failed to verify chat access: {e}")
                return
            except NetworkError as e:
                logger.error(f"Network error during initialization: {e}")
                return
            except Exception as e:
                logger.error(f"Unexpected error verifying chat access: {e}")
                return
                
        except TelegramError as e:
            logger.error(f"Failed to initialize Telegram bot: {e}")
        except Exception as e:
            logger.error(f"Unexpected error initializing Telegram bot: {e}")

    def _check_command_cooldown(self):
        """Check if enough time has passed since the last command"""
        current_time = time.time()
        time_since_last = current_time - self._last_command_time
        if time_since_last < self._command_cooldown:
            logger.debug(f"Command cooldown active. Time since last command: {time_since_last:.2f}s")
            return False
        self._last_command_time = current_time
        logger.debug("Command cooldown passed, allowing command")
        return True

    def _get_loop(self):
        """Get or create the event loop"""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    def handle_status_command(self, update, context):
        """Handle the /status command with detailed status reporting"""
        try:
            if not self._check_command_cooldown():
                update.message.reply_text("⏳ Please wait a moment before sending another command.")
                return

            if not self.initialized or not self.updater or not self.updater.running:
                logger.warning("Status command received but bot not properly initialized")
                update.message.reply_text("❌ Bot is not fully initialized yet. Please wait a moment and try again.")
                return

            if str(update.effective_chat.id) != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {update.effective_chat.id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            # Import here to avoid circular import
            from shared import kraken, DRY_RUN

            logger.info(f"Status command received from chat ID: {update.effective_chat.id}")
            
            # Send initial response
            update.message.reply_text("🔄 Fetching detailed status information...")
            
            try:
                # Fetch all required data
                logger.info("Fetching balance from Kraken...")
                balance = kraken.fetch_balance()
                
                # Get balances
                usdc_balance = balance.get('total', {}).get('USDC.F', 0)
                btc_balance = balance.get('total', {}).get('XBT.F', 0)
                eth_balance = balance.get('total', {}).get('ETH.F', 0)
                sol_balance = balance.get('total', {}).get('SOL', 0)
                eur_balance = balance.get('total', {}).get('EUR', 0)
                
                # Get current prices for value calculation only
                btc_eur = kraken.fetch_ticker('BTC/EUR')['last']
                eth_eur = kraken.fetch_ticker('ETH/EUR')['last']
                sol_eur = kraken.fetch_ticker('SOL/EUR')['last']
                usdc_eur = kraken.fetch_ticker('USDC/EUR')['last']
                
                # Calculate values in EUR
                btc_eur_value = btc_balance * btc_eur
                eth_eur_value = eth_balance * eth_eur
                sol_eur_value = sol_balance * sol_eur
                usdc_eur_value = usdc_balance * usdc_eur
                
                # Get recent trades
                recent_trades = kraken.fetch_closed_orders(limit=3)  # Last 3 trades
                
                # Calculate total portfolio value
                total_eur_value = (eur_balance + btc_eur_value + eth_eur_value + sol_eur_value + usdc_eur_value)
                
                # Prepare status message
                status_msg = (
                    "🤖 Detailed Bot Status\n\n"
                    f"🔹 System Status:\n"
                    f"• Mode: {'🟡 DRY RUN' if DRY_RUN else '🟢 LIVE'}\n"
                    f"• Bot State: {'🟢 Running' if self.updater and self.updater.running else '🔴 Stopped'}\n"
                    f"• Scheduling: {'🟢 Enabled' if self._scheduling_enabled else '🔴 Disabled'}\n"
                    f"• Last Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    
                    f"🔹 Portfolio Balances:\n"
                    f"EUR: {eur_balance:.2f} EUR\n"
                    f"USDC: {usdc_balance:.2f} USDC (≈ {usdc_eur_value:.2f} EUR)\n"
                    f"BTC: {btc_balance:.8f} BTC (≈ {btc_eur_value:.2f} EUR)\n"
                    f"ETH: {eth_balance:.8f} ETH (≈ {eth_eur_value:.2f} EUR)\n"
                    f"SOL: {sol_balance:.8f} SOL (≈ {sol_eur_value:.2f} EUR)\n\n"
                    
                    f"🔹 Total Portfolio Value:\n"
                    f"• {total_eur_value:.2f} EUR\n\n"
                )
                
                # Add recent trades if available
                if recent_trades:
                    status_msg += "🔹 Recent Trades:\n"
                    for trade in recent_trades:
                        symbol = trade['symbol']
                        side = "Buy" if trade['side'] == 'buy' else "Sell"
                        amount = float(trade['amount'])
                        price = float(trade['price'])
                        cost = amount * price
                        status = trade['status']
                        timestamp = datetime.fromtimestamp(trade['timestamp'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                        
                        status_msg += (
                            f"• {side} {symbol}\n"
                            f"  Amount: {amount:.8f}\n"
                            f"  Price: {price:.2f}\n"
                            f"  Total: {cost:.2f}\n"
                            f"  Status: {status}\n"
                            f"  Time: {timestamp}\n\n"
                        )
                else:
                    status_msg += "🔹 Recent Trades: No recent trades found\n\n"
                
                # Add system information
                status_msg += (
                    "🔹 System Information:\n"
                    f"• Command Cooldown: {self._command_cooldown} seconds\n"
                    f"• Price Check Cooldown: {self._price_check_cooldown} seconds\n"
                    f"• Bot Uptime: {self._get_bot_uptime()}\n"
                    f"• Last Command: {self._get_last_command_time()}\n"
                )
                
                # Send the status message
                logger.info("Sending detailed status message to Telegram")
                update.message.reply_text(status_msg)
                logger.info("Status command executed successfully")
                
            except Exception as e:
                error_msg = f"❌ Error fetching status information: {str(e)}"
                logger.error(f"Error in status command: {error_msg}", exc_info=True)
                update.message.reply_text(
                    f"❌ Error fetching status information:\n"
                    f"Error: {str(e)}\n\n"
                    f"Bot is still running in {'DRY RUN' if DRY_RUN else 'LIVE'} mode.\n"
                    f"Please try again in a few moments."
                )
                
        except Exception as e:
            error_msg = f"❌ Error in status command: {str(e)}"
            logger.error(f"Unexpected error in status command: {error_msg}", exc_info=True)
            try:
                update.message.reply_text(
                    f"❌ Error executing status command:\n"
                    f"Error: {str(e)}\n\n"
                    f"Please try again in a few moments."
                )
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}", exc_info=True)

    def _get_bot_uptime(self):
        """Calculate and format the bot's uptime"""
        if not hasattr(self, '_start_time'):
            self._start_time = time.time()
        
        uptime_seconds = int(time.time() - self._start_time)
        days = uptime_seconds // (24 * 3600)
        hours = (uptime_seconds % (24 * 3600)) // 3600
        minutes = (uptime_seconds % 3600) // 60
        seconds = uptime_seconds % 60
        
        if days > 0:
            return f"{days}d {hours}h {minutes}m {seconds}s"
        elif hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    def _get_last_command_time(self):
        """Format the last command time"""
        if self._last_command_time == 0:
            return "No commands executed yet"
        
        last_command = datetime.fromtimestamp(self._last_command_time)
        now = datetime.now()
        diff = now - last_command
        
        if diff.days > 0:
            return f"{diff.days} days ago"
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f"{hours} hours ago"
        elif diff.seconds > 60:
            minutes = diff.seconds // 60
            return f"{minutes} minutes ago"
        else:
            return f"{diff.seconds} seconds ago"

    def _parse_buy_command(self, args):
        """Parse buy command arguments
        Returns: (amount, currency, is_percentage) or (None, None, None) if invalid
        """
        if not args:
            return None, None, None
            
        try:
            # Parse amount
            amount_str = args[0].strip()
            is_percentage = False
            
            # Check if amount is a percentage
            if amount_str.endswith('%'):
                is_percentage = True
                amount_str = amount_str[:-1]
            
            amount = float(amount_str)
            if amount <= 0:
                return None, None, None
                
            # Parse currency if provided
            currency = None
            if len(args) > 1:
                currency = args[1].strip().upper()
                if currency not in ['EUR', 'USDC']:
                    return None, None, None
                    
            return amount, currency, is_percentage
        except (ValueError, IndexError):
            return None, None, None

    def handle_buy_command(self, update: Update, context: CallbackContext):
        """Handle the /buy command with confirmation"""
        chat_id = str(update.effective_chat.id)
        try:
            if not self._check_command_cooldown():
                update.message.reply_text("⏳ Please wait a moment before sending another command.")
                return

            if not self.initialized or not self.updater or not self.updater.running:
                logger.warning("Buy command received but bot not properly initialized")
                update.message.reply_text("❌ Bot is not fully initialized yet. Please wait a moment and try again.")
                return

            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            # Parse command arguments
            args = context.args if context.args else []
            amount, currency, is_percentage = self._parse_buy_command(args)
            
            if amount is None:
                update.message.reply_text(
                    "❌ Invalid command format. Use:\n"
                    "/buy [amount] [currency]\n"
                    "Examples:\n"
                    "/buy 100 EUR\n"
                    "/buy 50 USDC\n"
                    "/buy 25% EUR"
                )
                return

            # Store command details
            self._pending_buy_confirmation[chat_id] = (time.time(), 'buy', amount, currency, is_percentage)
            
            # Format confirmation message
            amount_str = f"{amount}%" if is_percentage else f"{amount:.2f}"
            currency_str = f" {currency}" if currency else " available EUR/USDC"
            update.message.reply_text(
                f"⚠️ Are you sure you want to execute a buy order for {amount_str}{currency_str}?\n"
                "Reply with /confirm within 30 seconds to proceed."
            )
            logger.info(f"Buy confirmation requested for chat {chat_id}: {amount_str}{currency_str}")
        except Exception as e:
            error_msg = f"❌ Error preparing buy order: {str(e)}"
            logger.error(error_msg)
            try:
                update.message.reply_text(error_msg)
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    def handle_buy_sol_command(self, update: Update, context: CallbackContext):
        """Handle the /buysol command with confirmation"""
        chat_id = str(update.effective_chat.id)
        try:
            if not self._check_command_cooldown():
                update.message.reply_text("⏳ Please wait a moment before sending another command.")
                return
            if not self.initialized or not self.updater or not self.updater.running:
                logger.warning("Buy SOL command received but bot not properly initialized")
                update.message.reply_text("❌ Bot is not fully initialized yet. Please wait a moment and try again.")
                return
            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            # Parse command arguments
            args = context.args if context.args else []
            amount, currency, is_percentage = self._parse_buy_command(args)
            
            if amount is None:
                update.message.reply_text(
                    "❌ Invalid command format. Use:\n"
                    "/buysol [amount] [currency]\n"
                    "Examples:\n"
                    "/buysol 100 EUR\n"
                    "/buysol 50 USDC\n"
                    "/buysol 25% EUR"
                )
                return

            # Store command details
            self._pending_buy_confirmation[chat_id] = (time.time(), 'buysol', amount, currency, is_percentage)
            
            # Format confirmation message
            amount_str = f"{amount}%" if is_percentage else f"{amount:.2f}"
            currency_str = f" {currency}" if currency else " available EUR/USDC"
            update.message.reply_text(
                f"⚠️ Are you sure you want to execute a SOL buy order for {amount_str}{currency_str}?\n"
                "Reply with /confirm within 30 seconds to proceed."
            )
            logger.info(f"Buy SOL confirmation requested for chat {chat_id}: {amount_str}{currency_str}")
        except Exception as e:
            error_msg = f"❌ Error preparing SOL buy order: {str(e)}"
            logger.error(error_msg)
            try:
                update.message.reply_text(error_msg)
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    def handle_buy_eth_command(self, update: Update, context: CallbackContext):
        """Handle the /buyeth command with confirmation"""
        chat_id = str(update.effective_chat.id)
        try:
            if not self._check_command_cooldown():
                update.message.reply_text("⏳ Please wait a moment before sending another command.")
                return
            if not self.initialized or not self.updater or not self.updater.running:
                logger.warning("Buy ETH command received but bot not properly initialized")
                update.message.reply_text("❌ Bot is not fully initialized yet. Please wait a moment and try again.")
                return
            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            # Parse command arguments
            args = context.args if context.args else []
            amount, currency, is_percentage = self._parse_buy_command(args)
            
            if amount is None:
                update.message.reply_text(
                    "❌ Invalid command format. Use:\n"
                    "/buyeth [amount] [currency]\n"
                    "Examples:\n"
                    "/buyeth 100 EUR\n"
                    "/buyeth 50 USDC\n"
                    "/buyeth 25% EUR"
                )
                return

            # Store command details
            self._pending_buy_confirmation[chat_id] = (time.time(), 'buyeth', amount, currency, is_percentage)
            
            # Format confirmation message
            amount_str = f"{amount}%" if is_percentage else f"{amount:.2f}"
            currency_str = f" {currency}" if currency else " available EUR/USDC"
            update.message.reply_text(
                f"⚠️ Are you sure you want to execute an ETH buy order for {amount_str}{currency_str}?\n"
                "Reply with /confirm within 30 seconds to proceed."
            )
            logger.info(f"Buy ETH confirmation requested for chat {chat_id}: {amount_str}{currency_str}")
        except Exception as e:
            error_msg = f"❌ Error preparing ETH buy order: {str(e)}"
            logger.error(error_msg)
            try:
                update.message.reply_text(error_msg)
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    def handle_buy_usdc_command(self, update: Update, context: CallbackContext):
        """Handle the /buyusdc command with confirmation"""
        chat_id = str(update.effective_chat.id)
        try:
            if not self._check_command_cooldown():
                update.message.reply_text("⏳ Please wait a moment before sending another command.")
                return
            if not self.initialized or not self.updater or not self.updater.running:
                logger.warning("Buy USDC command received but bot not properly initialized")
                update.message.reply_text("❌ Bot is not fully initialized yet. Please wait a moment and try again.")
                return
            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            # Parse command arguments
            args = context.args if context.args else []
            amount, currency, is_percentage = self._parse_buy_command(args)
            
            if amount is None:
                update.message.reply_text(
                    "❌ Invalid command format. Use:\n"
                    "/buyusdc [amount] [currency]\n"
                    "Examples:\n"
                    "/buyusdc 100 EUR\n"
                    "/buyusdc 25% EUR"
                )
                return

            # USDC can only be bought with EUR
            if currency and currency != 'EUR':
                update.message.reply_text("❌ USDC can only be bought with EUR")
                return

            # Store command details
            self._pending_buy_confirmation[chat_id] = (time.time(), 'buyusdc', amount, 'EUR', is_percentage)
            
            # Format confirmation message
            amount_str = f"{amount}%" if is_percentage else f"{amount:.2f}"
            update.message.reply_text(
                f"⚠️ Are you sure you want to execute a USDC buy order for {amount_str} EUR?\n"
                "Reply with /confirm within 30 seconds to proceed."
            )
            logger.info(f"Buy USDC confirmation requested for chat {chat_id}: {amount_str} EUR")
        except Exception as e:
            error_msg = f"❌ Error preparing USDC buy order: {str(e)}"
            logger.error(error_msg)
            try:
                update.message.reply_text(error_msg)
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    def handle_confirm_command(self, update: Update, context: CallbackContext):
        """Handle the /confirm command to execute a pending buy"""
        chat_id = str(update.effective_chat.id)
        try:
            pending = self._pending_buy_confirmation.get(chat_id)
            if not pending:
                update.message.reply_text("❌ No pending buy order to confirm or confirmation timed out.")
                logger.info(f"No pending buy order or confirmation timed out for chat {chat_id}")
                return
            ts, command_type, amount, currency, is_percentage = pending
            if (time.time() - ts) > 30:
                update.message.reply_text("❌ Confirmation timed out. Please try again.")
                logger.info(f"Confirmation timed out for chat {chat_id}")
                self._pending_buy_confirmation.pop(chat_id, None)
                return
            self._pending_buy_confirmation.pop(chat_id, None)
            # Determine which callback to use based on the stored command type
            if command_type == 'buysol':
                callback = self._buy_sol_callback
            elif command_type == 'buyeth':
                callback = self._buy_eth_callback
            elif command_type == 'buyusdc':
                callback = self._buy_usdc_callback
            else:
                callback = self._buy_callback
            if not callback:
                update.message.reply_text("❌ Buy functionality not initialized. Please contact the administrator.")
                logger.error("Buy callback not set")
                return
            update.message.reply_text("🔄 Confirmed. Initiating buy order...")
            logger.info(f"Buy confirmed by chat {chat_id}, initiating {command_type} order...")
            from shared import get_event_loop
            loop = get_event_loop()
            asyncio.run_coroutine_threadsafe(callback(amount, currency, is_percentage), loop)
            update.message.reply_text("✅ Buy order process initiated. Check the logs for details.")
            logger.info(f"{command_type} command executed successfully")
        except Exception as e:
            error_msg = f"❌ Error executing buy order: {str(e)}"
            logger.error(error_msg)
            try:
                update.message.reply_text(error_msg)
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    def handle_enable_command(self, update: Update, context: CallbackContext):
        """Handle the /enable command to enable bot scheduling"""
        chat_id = str(update.effective_chat.id)
        try:
            if not self._check_command_cooldown():
                update.message.reply_text("⏳ Please wait a moment before sending another command.")
                return

            if not self.initialized or not self.updater or not self.updater.running:
                logger.warning("Enable command received but bot not properly initialized")
                update.message.reply_text("❌ Bot is not fully initialized yet. Please wait a moment and try again.")
                return

            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            if self._scheduling_enabled:
                update.message.reply_text("ℹ️ Bot scheduling is already enabled.")
                return

            if not self._scheduling_state_callback:
                update.message.reply_text("❌ Bot scheduling control not initialized. Please contact the administrator.")
                logger.error("Scheduling state callback not set")
                return

            # Enable scheduling
            self._scheduling_enabled = True
            self._scheduling_state_callback(True)
            
            update.message.reply_text("✅ Bot scheduling has been enabled.")
            logger.info(f"Bot scheduling enabled by chat {chat_id}")
            
            # Send notification about the change
            asyncio.run_coroutine_threadsafe(
                self.send_notification("Bot scheduling has been enabled.", "INFO"),
                self._get_loop()
            )
            
        except Exception as e:
            error_msg = f"❌ Error enabling bot scheduling: {str(e)}"
            logger.error(error_msg)
            try:
                update.message.reply_text(error_msg)
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    def handle_disable_command(self, update: Update, context: CallbackContext):
        """Handle the /disable command to disable bot scheduling"""
        chat_id = str(update.effective_chat.id)
        try:
            if not self._check_command_cooldown():
                update.message.reply_text("⏳ Please wait a moment before sending another command.")
                return

            if not self.initialized or not self.updater or not self.updater.running:
                logger.warning("Disable command received but bot not properly initialized")
                update.message.reply_text("❌ Bot is not fully initialized yet. Please wait a moment and try again.")
                return

            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            if not self._scheduling_enabled:
                update.message.reply_text("ℹ️ Bot scheduling is already disabled.")
                return

            if not self._scheduling_state_callback:
                update.message.reply_text("❌ Bot scheduling control not initialized. Please contact the administrator.")
                logger.error("Scheduling state callback not set")
                return

            # Disable scheduling
            self._scheduling_enabled = False
            self._scheduling_state_callback(False)
            
            update.message.reply_text("✅ Bot scheduling has been disabled.")
            logger.info(f"Bot scheduling disabled by chat {chat_id}")
            
            # Send notification about the change
            asyncio.run_coroutine_threadsafe(
                self.send_notification("Bot scheduling has been disabled.", "WARNING"),
                self._get_loop()
            )
            
        except Exception as e:
            error_msg = f"❌ Error disabling bot scheduling: {str(e)}"
            logger.error(error_msg)
            try:
                update.message.reply_text(error_msg)
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    def set_scheduling_state_callback(self, callback):
        """Set the callback function to be called when scheduling state changes"""
        self._scheduling_state_callback = callback

    def is_scheduling_enabled(self):
        """Check if bot scheduling is currently enabled"""
        return self._scheduling_enabled

    def set_buy_callback(self, callback):
        """Set the callback function to be called when a buy order is confirmed"""
        self._buy_callback = callback

    def set_buy_sol_callback(self, callback):
        """Set the callback function to be called when a SOL buy order is confirmed"""
        self._buy_sol_callback = callback

    def set_buy_eth_callback(self, callback):
        """Set the callback function to be called when an ETH buy order is confirmed"""
        self._buy_eth_callback = callback

    def set_buy_usdc_callback(self, callback):
        """Set the callback function to be called when a USDC buy order is confirmed"""
        self._buy_usdc_callback = callback

    async def send_notification(self, message, level="INFO"):
        """Send a notification to the configured Telegram chat
        
        Args:
            message (str): The message to send
            level (str): The level of the notification (INFO, WARNING, ERROR, SUCCESS)
        """
        if not NOTIFICATION_CONFIG['telegram_enabled']:
            logger.debug("Telegram notifications are disabled")
            return

        if not self.initialized or not self.telegram_bot:
            logger.warning("Attempting to send notification but bot not initialized")
            await self.initialize()
            if not self.initialized or not self.telegram_bot:
                logger.error("Failed to initialize bot for notification")
                return

        try:
            # Add emoji based on level
            emoji = {
                "INFO": "ℹ️",
                "WARNING": "⚠️",
                "ERROR": "❌",
                "SUCCESS": "✅"
            }.get(level, "ℹ️")

            formatted_message = f"{emoji} {message}"
            
            # Send the message using synchronous method
            self.telegram_bot.send_message(
                chat_id=NOTIFICATION_CONFIG['telegram_chat_id'],
                text=formatted_message
            )
            logger.info(f"Notification sent successfully: {message}")
            
        except BadRequest as e:
            if "chat not found" in str(e).lower():
                logger.error(
                    f"Chat not found. Please make sure:\n"
                    f"1. You have started a chat with the bot\n"
                    f"2. The chat ID {NOTIFICATION_CONFIG['telegram_chat_id']} is correct\n"
                    f"3. You have sent at least one message to the bot"
                )
            else:
                logger.error(f"Failed to send notification: {e}")
        except NetworkError as e:
            logger.error(f"Network error sending notification: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending notification: {e}")

    def handle_start_command(self, update: Update, context: CallbackContext):
        """Handle the /start command"""
        chat_id = str(update.effective_chat.id)
        try:
            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            welcome_message = (
                "👋 Welcome to the Kraken Trading Bot!\n\n"
                "This bot helps you manage your cryptocurrency trades on Kraken.\n\n"
                "🔹 Main Features:\n"
                "• Buy BTC, ETH, SOL, and USDC\n"
                "• Check prices and balances\n"
                "• View trading history\n"
                "• Automated scheduled trading\n\n"
                "📝 Quick Start:\n"
                "1. Use /help to see all available commands\n"
                "2. Use /price to check current prices\n"
                "3. Use /balance to check your balances\n"
                "4. Use /buy, /buysol, /buyeth, or /buyusdc to make trades\n\n"
                "⚠️ Important:\n"
                "• All trades require confirmation\n"
                "• Minimum trade amount is 10 EUR/USDC\n"
                "• Bot scheduling can be enabled/disabled\n\n"
                "Need help? Use /help for detailed command information."
            )
            update.message.reply_text(welcome_message)
            logger.info(f"Start command executed for chat {chat_id}")
        except Exception as e:
            logger.error(f"Error in start command: {e}")
            update.message.reply_text("❌ An error occurred. Please try again.")

    def handle_help_command(self, update: Update, context: CallbackContext):
        """Handle the /help command"""
        chat_id = str(update.effective_chat.id)
        try:
            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            help_message = (
                "📚 Command Help\n\n"
                "🔹 Trading Commands:\n"
                "/buy - Buy BTC\n"
                "• Uses available EUR or USDC balance\n"
                "• Requires confirmation\n"
                "• Minimum 10 EUR/USDC required\n\n"
                "/buysol - Buy SOL\n"
                "• Uses available EUR or USDC balance\n"
                "• Requires confirmation\n"
                "• Minimum 10 EUR/USDC required\n\n"
                "/buyeth - Buy ETH\n"
                "• Uses available EUR or USDC balance\n"
                "• Requires confirmation\n"
                "• Minimum 10 EUR/USDC required\n\n"
                "/buyusdc - Buy USDC\n"
                "• Uses EUR balance only\n"
                "• Requires confirmation\n"
                "• Minimum 10 EUR required\n\n"
                "🔹 Information Commands:\n"
                "/price - Check current prices\n"
                "• Shows BTC, ETH, SOL prices in EUR and USDC\n"
                "• 10-second cooldown between checks\n\n"
                "/balance - Check your balances\n"
                "• Shows available EUR, USDC, BTC, ETH, SOL balances\n"
                "• Includes approximate values in EUR\n\n"
                "/status - Full status check\n"
                "• Shows all balances and current prices\n"
                "• Includes bot status and scheduling state\n\n"
                "/history - View trading history\n"
                "• Shows recent trades and their status\n"
                "• Includes order details and timestamps\n\n"
                "🔹 Control Commands:\n"
                "/enable - Enable bot scheduling\n"
                "• Enables automatic Monday/Sunday trading\n"
                "• Requires confirmation\n\n"
                "/disable - Disable bot scheduling\n"
                "• Disables automatic trading\n"
                "• Requires confirmation\n\n"
                "⚠️ Important Notes:\n"
                "• All trades require /confirm within 30 seconds\n"
                "• Minimum trade amount is 10 EUR/USDC\n"
                "• Price checks have a 10-second cooldown\n"
                "• Bot scheduling can be enabled/disabled\n"
                "• All commands are logged for security"
            )
            update.message.reply_text(help_message)
            logger.info(f"Help command executed for chat {chat_id}")
        except Exception as e:
            logger.error(f"Error in help command: {e}")
            update.message.reply_text("❌ An error occurred. Please try again.")

    def handle_price_command(self, update: Update, context: CallbackContext):
        """Handle the /price command to check current prices"""
        chat_id = str(update.effective_chat.id)
        try:
            if not self._check_command_cooldown():
                update.message.reply_text("⏳ Please wait a moment before sending another command.")
                return

            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            # Check price check cooldown
            last_check = self._last_price_check.get(chat_id, 0)
            if time.time() - last_check < self._price_check_cooldown:
                remaining = int(self._price_check_cooldown - (time.time() - last_check))
                update.message.reply_text(f"⏳ Please wait {remaining} seconds before checking prices again.")
                return

            self._last_price_check[chat_id] = time.time()

            # Import here to avoid circular import
            from shared import kraken

            # Send initial response
            update.message.reply_text("🔄 Fetching current prices...")
            
            try:
                # Fetch current prices
                btc_eur = kraken.fetch_ticker('BTC/EUR')['last']
                btc_usdc = kraken.fetch_ticker('BTC/USDC')['last']
                eth_eur = kraken.fetch_ticker('ETH/EUR')['last']
                eth_usdc = kraken.fetch_ticker('ETH/USDC')['last']
                sol_eur = kraken.fetch_ticker('SOL/EUR')['last']
                sol_usdc = kraken.fetch_ticker('SOL/USDC')['last']
                usdc_eur = kraken.fetch_ticker('USDC/EUR')['last']

                price_message = (
                    "💰 Current Prices:\n\n"
                    f"Bitcoin (BTC):\n"
                    f"• {btc_eur:.2f} EUR\n"
                    f"• {btc_usdc:.2f} USDC\n\n"
                    f"Ethereum (ETH):\n"
                    f"• {eth_eur:.2f} EUR\n"
                    f"• {eth_usdc:.2f} USDC\n\n"
                    f"Solana (SOL):\n"
                    f"• {sol_eur:.2f} EUR\n"
                    f"• {sol_usdc:.2f} USDC\n\n"
                    f"USDC:\n"
                    f"• {usdc_eur:.4f} EUR\n\n"
                    f"Last Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                update.message.reply_text(price_message)
                logger.info(f"Price command executed successfully for chat {chat_id}")
                
            except Exception as e:
                error_msg = f"❌ Error fetching prices: {str(e)}"
                logger.error(error_msg)
                update.message.reply_text(error_msg)
                
        except Exception as e:
            logger.error(f"Error in price command: {e}")
            update.message.reply_text("❌ An error occurred. Please try again.")

    def handle_balance_command(self, update: Update, context: CallbackContext):
        """Handle the /balance command to check balances"""
        chat_id = str(update.effective_chat.id)
        try:
            if not self._check_command_cooldown():
                update.message.reply_text("⏳ Please wait a moment before sending another command.")
                return

            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            # Import here to avoid circular import
            from shared import kraken

            # Send initial response
            update.message.reply_text("🔄 Fetching balances...")
            
            try:
                # Fetch balances
                balance = kraken.fetch_balance()
                eur_balance = balance['total'].get('EUR', 0)
                usdc_balance = balance['total'].get('USDC.F', 0)
                btc_balance = balance['total'].get('XBT.F', 0)
                eth_balance = balance['total'].get('ETH.F', 0)
                sol_balance = balance['total'].get('SOL', 0)

                # Get current prices for value calculation
                btc_eur = kraken.fetch_ticker('BTC/EUR')['last']
                eth_eur = kraken.fetch_ticker('ETH/EUR')['last']
                sol_eur = kraken.fetch_ticker('SOL/EUR')['last']
                usdc_eur = kraken.fetch_ticker('USDC/EUR')['last']

                # Calculate EUR values
                btc_eur_value = btc_balance * btc_eur
                eth_eur_value = eth_balance * eth_eur
                sol_eur_value = sol_balance * sol_eur
                usdc_eur_value = usdc_balance * usdc_eur

                balance_message = (
                    "💰 Your Balances:\n\n"
                    f"EUR: {eur_balance:.2f} EUR\n"
                    f"USDC: {usdc_balance:.2f} USDC (≈ {usdc_eur_value:.2f} EUR)\n"
                    f"BTC: {btc_balance:.8f} BTC (≈ {btc_eur_value:.2f} EUR)\n"
                    f"ETH: {eth_balance:.8f} ETH (≈ {eth_eur_value:.2f} EUR)\n"
                    f"SOL: {sol_balance:.8f} SOL (≈ {sol_eur_value:.2f} EUR)\n\n"
                    f"Total Value: {(eur_balance + btc_eur_value + eth_eur_value + sol_eur_value + usdc_eur_value):.2f} EUR\n\n"
                    f"Last Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                update.message.reply_text(balance_message)
                logger.info(f"Balance command executed successfully for chat {chat_id}")
                
            except Exception as e:
                error_msg = f"❌ Error fetching balances: {str(e)}"
                logger.error(error_msg)
                update.message.reply_text(error_msg)
                
        except Exception as e:
            logger.error(f"Error in balance command: {e}")
            update.message.reply_text("❌ An error occurred. Please try again.")

    def handle_history_command(self, update: Update, context: CallbackContext):
        """Handle the /history command to view trading history"""
        chat_id = str(update.effective_chat.id)
        try:
            if not self._check_command_cooldown():
                update.message.reply_text("⏳ Please wait a moment before sending another command.")
                return

            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            # Import here to avoid circular import
            from shared import kraken

            # Send initial response
            update.message.reply_text("🔄 Fetching trading history...")
            
            try:
                # Fetch recent orders
                orders = kraken.fetch_closed_orders(limit=5)  # Get last 5 closed orders
                
                if not orders:
                    update.message.reply_text("📝 No recent trading history found.")
                    return

                history_message = "📝 Recent Trading History:\n\n"
                
                for order in orders:
                    symbol = order['symbol']
                    side = "Buy" if order['side'] == 'buy' else "Sell"
                    amount = float(order['amount'])
                    price = float(order['price'])
                    cost = amount * price
                    status = order['status']
                    timestamp = datetime.fromtimestamp(order['timestamp'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                    
                    history_message += (
                        f"🔹 {side} {symbol}\n"
                        f"• Amount: {amount:.8f}\n"
                        f"• Price: {price:.2f}\n"
                        f"• Total: {cost:.2f}\n"
                        f"• Status: {status}\n"
                        f"• Time: {timestamp}\n\n"
                    )

                history_message += f"Last Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                update.message.reply_text(history_message)
                logger.info(f"History command executed successfully for chat {chat_id}")
                
            except Exception as e:
                error_msg = f"❌ Error fetching trading history: {str(e)}"
                logger.error(error_msg)
                update.message.reply_text(error_msg)
                
        except Exception as e:
            logger.error(f"Error in history command: {e}")
            update.message.reply_text("❌ An error occurred. Please try again.")

# Create a global notification manager instance
notification_manager = NotificationManager()

# Handle shutdown gracefully
def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down...")
    notification_manager.stop()

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# Send a test notification on startup
def send_test_notification():
    """Send a test notification to verify the setup"""
    if NOTIFICATION_CONFIG['telegram_enabled']:
        try:
            logger.info("Sending test notification...")
            asyncio.run(notification_manager.initialize())
            if notification_manager.initialized and not notification_manager._startup_notification_sent:
                asyncio.run(notification_manager.send_notification(
                    "🔔 Bot is ready! Available commands:\n"
                    "/buy - Trigger a manual BTC buy order\n"
                    "/buysol - Trigger a SOL buy order\n"
                    "/buyeth - Trigger a ETH buy order\n"
                    "/buyusdc - Trigger a USDC buy order\n"
                    "/status - Check bot status",
                    "SUCCESS"
                ))
                notification_manager._startup_notification_sent = True
                logger.info("Test notification sent successfully")
            else:
                logger.info("Test notification already sent, skipping")
        except Exception as e:
            logger.error(f"Failed to send test notification: {e}")

# Run the test notification
try:
    send_test_notification()
except Exception as e:
    logger.error(f"Error running test notification: {e}") 