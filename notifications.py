import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.error import TelegramError, BadRequest
from config import NOTIFICATION_CONFIG
import asyncio
import threading

logger = logging.getLogger(__name__)

class NotificationManager:
    def __init__(self):
        self.telegram_bot = None
        self.initialized = False
        self.updater = None
        self._polling_thread = None

    def _start_polling(self):
        """Start polling in a separate thread"""
        try:
            logger.info("Starting Telegram bot polling...")
            self.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot polling started successfully")
        except Exception as e:
            logger.error(f"Error in polling thread: {e}")

    async def initialize(self):
        """Initialize the Telegram bot asynchronously"""
        if self.initialized:
            return

        if NOTIFICATION_CONFIG['telegram_enabled']:
            if not NOTIFICATION_CONFIG['telegram_token']:
                logger.error("Telegram bot token is not set")
                return
            if not NOTIFICATION_CONFIG['telegram_chat_id']:
                logger.error("Telegram chat ID is not set")
                return
                
            try:
                # Initialize the bot and updater
                self.updater = Updater(token=NOTIFICATION_CONFIG['telegram_token'], use_context=True)
                self.telegram_bot = self.updater.bot
                
                # Add command handlers
                dispatcher = self.updater.dispatcher
                dispatcher.add_handler(CommandHandler("buy", self.handle_buy_command))
                dispatcher.add_handler(CommandHandler("status", self.handle_status_command))
                
                # Test the connection and verify chat
                logger.info("Testing Telegram bot connection...")
                bot_info = self.telegram_bot.get_me()
                logger.info(f"Telegram bot connection successful. Bot username: @{bot_info.username}")
                
                # Verify chat access
                try:
                    self.telegram_bot.send_message(
                        chat_id=NOTIFICATION_CONFIG['telegram_chat_id'],
                        text="🔔 Bot is starting up and testing notifications...\n\n"
                             "Available commands:\n"
                             "/buy - Trigger a manual buy order\n"
                             "/status - Check bot status"
                    )
                    logger.info("Successfully verified chat access")
                    
                    # Start polling in a separate thread
                    self._polling_thread = threading.Thread(target=self._start_polling, daemon=True)
                    self._polling_thread.start()
                    
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
                except Exception as e:
                    logger.error(f"Unexpected error verifying chat access: {e}")
                    return
                
                self.initialized = True
                logger.info("Telegram bot initialization completed successfully")
            except TelegramError as e:
                logger.error(f"Failed to initialize Telegram bot: {e}")
            except Exception as e:
                logger.error(f"Unexpected error initializing Telegram bot: {e}")
        else:
            logger.info("Telegram notifications are disabled")

    def handle_buy_command(self, update: Update, context: CallbackContext):
        """Handle the /buy command"""
        if not self.initialized:
            update.message.reply_text("❌ Bot is not fully initialized yet. Please wait a moment and try again.")
            return

        if str(update.effective_chat.id) != str(NOTIFICATION_CONFIG['telegram_chat_id']):
            update.message.reply_text("❌ Unauthorized access. This bot is private.")
            return

        try:
            # Import here to avoid circular import
            from bot import place_limit_order, get_event_loop
            
            update.message.reply_text("🔄 Initiating manual buy order...")
            loop = get_event_loop()
            asyncio.run_coroutine_threadsafe(place_limit_order(), loop)
            update.message.reply_text("✅ Buy order process initiated. Check the logs for details.")
        except Exception as e:
            error_msg = f"❌ Error executing buy order: {str(e)}"
            logger.error(error_msg)
            update.message.reply_text(error_msg)

    def handle_status_command(self, update: Update, context: CallbackContext):
        """Handle the /status command"""
        if not self.initialized:
            update.message.reply_text("❌ Bot is not fully initialized yet. Please wait a moment and try again.")
            return

        if str(update.effective_chat.id) != str(NOTIFICATION_CONFIG['telegram_chat_id']):
            update.message.reply_text("❌ Unauthorized access. This bot is private.")
            return

        try:
            # Import here to avoid circular import
            from bot import kraken, DRY_RUN, TEST_MODE
            
            balance = kraken.fetch_balance()
            eur_balance = balance['total'].get('EUR', 0)
            btc_balance = balance['total'].get('BTC', 0)
            
            mode = "DRY RUN" if DRY_RUN else "TEST MODE" if TEST_MODE else "LIVE"
            status_msg = (
                f"🤖 Bot Status:\n\n"
                f"Mode: {mode}\n"
                f"EUR Balance: {eur_balance:.2f} EUR\n"
                f"BTC Balance: {btc_balance:.8f} BTC\n"
                f"Bot Status: {'Initialized' if self.initialized else 'Initializing...'}"
            )
            update.message.reply_text(status_msg)
        except Exception as e:
            error_msg = f"❌ Error fetching status: {str(e)}"
            logger.error(error_msg)
            update.message.reply_text(error_msg)

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

# Create a global notification manager instance
notification_manager = NotificationManager()

# Send a test notification on startup
def send_test_notification():
    """Send a test notification to verify the setup"""
    if NOTIFICATION_CONFIG['telegram_enabled']:
        try:
            asyncio.run(notification_manager.initialize())
            if notification_manager.initialized:
                asyncio.run(notification_manager.send_notification(
                    "🔔 Bot is ready! Available commands:\n"
                    "/buy - Trigger a manual buy order\n"
                    "/status - Check bot status",
                    "SUCCESS"
                ))
        except Exception as e:
            logger.error(f"Failed to send test notification: {e}")

# Run the test notification
try:
    send_test_notification()
except Exception as e:
    logger.error(f"Error running test notification: {e}") 