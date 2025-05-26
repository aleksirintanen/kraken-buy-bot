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
        self._pending_buy_confirmation = {}  # chat_id: (timestamp, command_type)
        self._pending_eur_convert_confirmation = {}  # chat_id: (timestamp, amount)
        self._buy_callback = None  # Callback for buy orders
        self._buy_min_callback = None  # Callback for minimum buy orders
        self._eur_convert_callback = None  # Callback for EUR conversion
        self._scheduling_enabled = True  # Track if bot scheduling is enabled
        self._scheduling_state_callback = None  # Callback to control bot scheduling

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
            dispatcher.add_handler(CommandHandler("buy", self.handle_buy_command))
            dispatcher.add_handler(CommandHandler("buymin", self.handle_buy_min_command))
            dispatcher.add_handler(CommandHandler("status", self.handle_status_command))
            dispatcher.add_handler(CommandHandler("confirm", self.handle_confirm_command))
            dispatcher.add_handler(CommandHandler("confirm_eur", self.handle_eur_convert_confirm_command))
            dispatcher.add_handler(CommandHandler("convert_eur", self.handle_convert_eur_command))
            dispatcher.add_handler(CommandHandler("enable", self.handle_enable_command))
            dispatcher.add_handler(CommandHandler("disable", self.handle_disable_command))
            
            # Test the connection and verify chat
            logger.info("Testing Telegram bot connection...")
            bot_info = self.telegram_bot.get_me()
            logger.info(f"Telegram bot connection successful. Bot username: @{bot_info.username}")
            
            # Verify chat access and send startup notification if not sent yet
            try:
                startup_message = (
                    "🔔 Bot is starting up and testing notifications...\n\n"
                    "Available commands:\n"
                    "/buy - Trigger a manual buy order\n"
                    "/buymin - Trigger a minimum BTC buy order\n"
                    "/convert_eur [amount] - Convert EUR to USDC (optional amount)\n"
                    "/status - Check bot status\n"
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
        """Handle the /status command"""
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
            update.message.reply_text("🔄 Fetching status and balances...")
            
            try:
                logger.info("Fetching balance from Kraken...")
                # Fetch balance
                balance = kraken.fetch_balance()
                logger.debug(f"Raw balance response: {balance}")
                
                usdc_balance = balance.get('total', {}).get('USDC.F', 0)
                btc_balance = balance.get('total', {}).get('XBT.F', 0)
                eur_balance = balance.get('total', {}).get('EUR', 0)
                logger.info(f"Retrieved balances - USDC: {usdc_balance}, BTC: {btc_balance}, EUR: {eur_balance}")
                
                logger.info("Fetching current BTC price...")
                # Get current price
                ticker = kraken.fetch_ticker('BTC/USDC')
                current_price = ticker.get('last', 0)
                logger.info(f"Current BTC price: {current_price}")

                # Get EUR/USDC price for EUR value calculation
                eur_ticker = kraken.fetch_ticker('USDC/EUR')
                eur_usdc_price = eur_ticker.get('last', 0)
                eur_value_usdc = eur_balance * eur_usdc_price if eur_usdc_price else 0
                
                mode = "DRY RUN" if DRY_RUN else "LIVE"
                status_msg = (
                    f"🤖 Bot Status:\n\n"
                    f"Mode: {mode}\n"
                    f"Scheduling: {'Enabled' if self._scheduling_enabled else 'Disabled'}\n"
                    f"Current BTC Price: {current_price:.2f} USDC\n\n"
                    f"Balances:\n"
                    f"USDC: {usdc_balance:.2f} USDC\n"
                    f"BTC: {btc_balance:.8f} BTC (≈ {btc_balance * current_price:.2f} USDC)\n"
                    f"EUR: {eur_balance:.2f} EUR (≈ {eur_value_usdc:.2f} USDC)\n\n"
                    f"Total Value: {(usdc_balance + btc_balance * current_price + eur_value_usdc):.2f} USDC\n"
                    f"Bot Status: {'Running' if self.updater and self.updater.running else 'Stopped'}\n"
                    f"Last Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                logger.info("Sending status message to Telegram")
                update.message.reply_text(status_msg)
                logger.info("Status command executed successfully")
                
            except Exception as e:
                error_msg = f"❌ Error fetching balances: {str(e)}"
                logger.error(f"Error in status command: {error_msg}", exc_info=True)
                update.message.reply_text(
                    f"❌ Error fetching balances:\n"
                    f"Error: {str(e)}\n\n"
                    f"Bot is still running in {mode} mode.\n"
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

            # Prompt for confirmation
            self._pending_buy_confirmation[chat_id] = (time.time(), 'buy')
            update.message.reply_text(
                "⚠️ Are you sure you want to execute a buy order?\n"
                "Reply with /confirm within 30 seconds to proceed."
            )
            logger.info(f"Buy confirmation requested for chat {chat_id}")
        except Exception as e:
            error_msg = f"❌ Error preparing buy order: {str(e)}"
            logger.error(error_msg)
            try:
                update.message.reply_text(error_msg)
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    def handle_buy_min_command(self, update: Update, context: CallbackContext):
        """Handle the /buymin command with confirmation"""
        chat_id = str(update.effective_chat.id)
        try:
            if not self._check_command_cooldown():
                update.message.reply_text("⏳ Please wait a moment before sending another command.")
                return

            if not self.initialized or not self.updater or not self.updater.running:
                logger.warning("Buy min command received but bot not properly initialized")
                update.message.reply_text("❌ Bot is not fully initialized yet. Please wait a moment and try again.")
                return

            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            # Prompt for confirmation
            self._pending_buy_confirmation[chat_id] = (time.time(), 'buymin')
            update.message.reply_text(
                "⚠️ Are you sure you want to execute a minimum BTC buy order?\n"
                "Reply with /confirm within 30 seconds to proceed."
            )
            logger.info(f"Buy min confirmation requested for chat {chat_id}")
        except Exception as e:
            error_msg = f"❌ Error preparing minimum buy order: {str(e)}"
            logger.error(error_msg)
            try:
                update.message.reply_text(error_msg)
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    def handle_confirm_command(self, update: Update, context: CallbackContext):
        """Handle the /confirm command to execute a pending buy"""
        chat_id = str(update.effective_chat.id)
        try:
            # Check if there is a pending confirmation and it's within 30 seconds
            pending = self._pending_buy_confirmation.get(chat_id)
            if not pending:
                update.message.reply_text("❌ No pending buy order to confirm or confirmation timed out.")
                logger.info(f"No pending buy order or confirmation timed out for chat {chat_id}")
                return
                
            ts, command_type = pending
            if (time.time() - ts) > 30:
                update.message.reply_text("❌ Confirmation timed out. Please try again.")
                logger.info(f"Confirmation timed out for chat {chat_id}")
                self._pending_buy_confirmation.pop(chat_id, None)
                return

            # Remove pending confirmation
            self._pending_buy_confirmation.pop(chat_id, None)
            
            # Determine which callback to use based on the stored command type
            callback = self._buy_min_callback if command_type == 'buymin' else self._buy_callback
            
            if not callback:
                update.message.reply_text("❌ Buy functionality not initialized. Please contact the administrator.")
                logger.error("Buy callback not set")
                return

            update.message.reply_text("🔄 Confirmed. Initiating buy order...")
            logger.info(f"Buy confirmed by chat {chat_id}, initiating {command_type} order...")
            
            # Use the callback to execute the buy order
            from shared import get_event_loop
            loop = get_event_loop()
            asyncio.run_coroutine_threadsafe(callback(), loop)
            update.message.reply_text("✅ Buy order process initiated. Check the logs for details.")
            logger.info(f"{command_type} command executed successfully")
        except Exception as e:
            error_msg = f"❌ Error executing buy order: {str(e)}"
            logger.error(error_msg)
            try:
                update.message.reply_text(error_msg)
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    def handle_eur_convert_confirm_command(self, update: Update, context: CallbackContext):
        """Handle the /confirm_eur command to execute a pending EUR conversion"""
        chat_id = str(update.effective_chat.id)
        try:
            # Check if there is a pending confirmation and it's within 6 hours
            pending = self._pending_eur_convert_confirmation.get(chat_id)
            if not pending:
                update.message.reply_text("❌ No pending EUR conversion to confirm or confirmation timed out.")
                logger.info(f"No pending EUR conversion or confirmation timed out for chat {chat_id}")
                return
                
            ts, amount = pending
            if (time.time() - ts) > (6 * 3600):  # 6 hours in seconds
                update.message.reply_text("❌ EUR conversion confirmation timed out. Please try again.")
                logger.info(f"EUR conversion confirmation timed out for chat {chat_id}")
                self._pending_eur_convert_confirmation.pop(chat_id, None)
                return

            # Remove pending confirmation
            self._pending_eur_convert_confirmation.pop(chat_id, None)
            
            if not self._eur_convert_callback:
                update.message.reply_text("❌ EUR conversion functionality not initialized. Please contact the administrator.")
                logger.error("EUR convert callback not set")
                return

            update.message.reply_text("🔄 Confirmed. Initiating EUR to USDC conversion...")
            logger.info(f"EUR conversion confirmed by chat {chat_id}, amount: {amount:.2f} EUR")
            
            # Use the callback to execute the conversion
            from shared import get_event_loop
            loop = get_event_loop()
            asyncio.run_coroutine_threadsafe(self._eur_convert_callback(amount), loop)
            update.message.reply_text("✅ EUR conversion process initiated. Check the logs for details.")
            logger.info("EUR conversion command executed successfully")
        except Exception as e:
            error_msg = f"❌ Error executing EUR conversion: {str(e)}"
            logger.error(error_msg)
            try:
                update.message.reply_text(error_msg)
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    def handle_convert_eur_command(self, update: Update, context: CallbackContext):
        """Handle the /convert_eur command to manually trigger EUR conversion"""
        chat_id = str(update.effective_chat.id)
        try:
            if not self._check_command_cooldown():
                update.message.reply_text("⏳ Please wait a moment before sending another command.")
                return

            if not self.initialized or not self.updater or not self.updater.running:
                logger.warning("Convert EUR command received but bot not properly initialized")
                update.message.reply_text("❌ Bot is not fully initialized yet. Please wait a moment and try again.")
                return

            if chat_id != str(NOTIFICATION_CONFIG['telegram_chat_id']):
                logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
                update.message.reply_text("❌ Unauthorized access. This bot is private.")
                return

            # Check if an amount was provided
            amount = None
            if context.args and len(context.args) > 0:
                try:
                    amount = float(context.args[0])
                    if amount <= 0:
                        update.message.reply_text("❌ Amount must be greater than 0")
                        return
                except ValueError:
                    update.message.reply_text("❌ Invalid amount. Please provide a valid number.")
                    return

            # Execute the conversion
            from shared import get_event_loop
            loop = get_event_loop()
            asyncio.run_coroutine_threadsafe(self._eur_convert_callback(amount), loop)
            update.message.reply_text(
                f"🔄 Initiating EUR to USDC conversion{' for ' + str(amount) + ' EUR' if amount else ''}...\n"
                "Check the logs for details."
            )
            logger.info(f"Manual EUR conversion initiated by chat {chat_id}{' for ' + str(amount) + ' EUR' if amount else ''}")
        except Exception as e:
            error_msg = f"❌ Error initiating EUR conversion: {str(e)}"
            logger.error(error_msg)
            try:
                update.message.reply_text(error_msg)
            except Exception as reply_error:
                logger.error(f"Failed to send error message: {reply_error}")

    async def request_eur_convert_confirmation(self, amount: float):
        """Request confirmation for EUR conversion if amount is over 150 EUR"""
        if amount <= 150:
            return True  # No confirmation needed for amounts <= 150 EUR
            
        chat_id = NOTIFICATION_CONFIG['telegram_chat_id']
        self._pending_eur_convert_confirmation[chat_id] = (time.time(), amount)
        
        try:
            await self.send_notification(
                f"⚠️ Large EUR to USDC conversion requested:\n"
                f"Amount: {amount:.2f} EUR\n\n"
                f"Reply with /confirm_eur within 6 hours to proceed.\n"
                f"If not confirmed, the conversion will be cancelled.",
                "WARNING"
            )
            return False  # Confirmation pending
        except Exception as e:
            logger.error(f"Failed to send EUR conversion confirmation request: {e}")
            return False  # Treat as not confirmed on error

    async def send_notification(self, message, level="INFO"):
        """Send notification through all enabled channels"""
        if not self.initialized and NOTIFICATION_CONFIG['telegram_enabled']:
            await self.initialize()

        if level == "ERROR":
            message = f"🚨 ERROR: {message}"
        elif level == "WARNING":
            message = f"⚠️ WARNING: {message}"
        elif level == "SUCCESS":
            message = f"✅ SUCCESS: {message}"

        # Send to Telegram
        if NOTIFICATION_CONFIG['telegram_enabled'] and self.initialized:
            try:
                logger.debug(f"Sending Telegram notification: {message}")
                self.telegram_bot.send_message(
                    chat_id=NOTIFICATION_CONFIG['telegram_chat_id'],
                    text=message,
                    parse_mode='HTML'
                )
                logger.debug("Telegram notification sent successfully")
            except TelegramError as e:
                logger.error(f"Telegram API error: {e}")
                # Try to reinitialize if we get a network error
                if isinstance(e, NetworkError):
                    self.initialized = False
                    await self.initialize()
            except Exception as e:
                logger.error(f"Unexpected error sending Telegram notification: {e}")

        # Send email
        if NOTIFICATION_CONFIG['email_enabled']:
            try:
                self._send_email(message, level)
            except Exception as e:
                logger.error(f"Failed to send email notification: {e}")

    def _send_email(self, message, level):
        """Send email notification"""
        if not all([
            NOTIFICATION_CONFIG['email_smtp_server'],
            NOTIFICATION_CONFIG['email_username'],
            NOTIFICATION_CONFIG['email_password'],
            NOTIFICATION_CONFIG['email_recipient']
        ]):
            return

        msg = MIMEMultipart()
        msg['From'] = NOTIFICATION_CONFIG['email_username']
        msg['To'] = NOTIFICATION_CONFIG['email_recipient']
        msg['Subject'] = f"Kraken Bot {level} Alert"

        msg.attach(MIMEText(message, 'plain'))

        with smtplib.SMTP(NOTIFICATION_CONFIG['email_smtp_server'], NOTIFICATION_CONFIG['email_smtp_port']) as server:
            server.starttls()
            server.login(NOTIFICATION_CONFIG['email_username'], NOTIFICATION_CONFIG['email_password'])
            server.send_message(msg)

    def set_buy_callback(self, callback):
        """Set the callback function to be called when a buy order is confirmed"""
        self._buy_callback = callback

    def set_buy_min_callback(self, callback):
        """Set the callback function to be called when a minimum buy order is confirmed"""
        self._buy_min_callback = callback

    def set_eur_convert_callback(self, callback):
        """Set the callback function to be called when EUR conversion is confirmed"""
        self._eur_convert_callback = callback

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
                    "/buy - Trigger a manual buy order\n"
                    "/buymin - Trigger a minimum BTC buy order\n"
                    "/convert_eur [amount] - Convert EUR to USDC (optional amount)\n"
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