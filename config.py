import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Trading Configuration
TRADING_CONFIG = {
    'symbol': os.getenv('TRADING_SYMBOL', 'BTC/USDC'),
    'min_btc_amount': float(os.getenv('MIN_BTC_AMOUNT', '0.00005')),
    'balance_percentage': float(os.getenv('BALANCE_PERCENTAGE', '20')) / 100,  # Convert percentage to decimal
    'order_timeout_minutes': int(os.getenv('ORDER_TIMEOUT_MINUTES', '5')),
    'max_retries': int(os.getenv('MAX_RETRIES', '10')),
    'retry_delay_seconds': int(os.getenv('RETRY_DELAY_SECONDS', '5')),
}

# Schedule Configuration
SCHEDULE_CONFIG = {
    'monday_time': os.getenv('MONDAY_TIME', '02:00'),
    'sunday_time': os.getenv('SUNDAY_TIME', '02:00'),
    'timezone': os.getenv('TZ', 'UTC'),
}

# Notification Configuration
NOTIFICATION_CONFIG = {
    'telegram_enabled': os.getenv('TELEGRAM_ENABLED', 'False').lower() == 'true',
    'telegram_token': os.getenv('TELEGRAM_BOT_TOKEN', ''),
    'telegram_chat_id': os.getenv('TELEGRAM_CHAT_ID', ''),
    'email_enabled': os.getenv('EMAIL_ENABLED', 'False').lower() == 'true',
    'email_smtp_server': os.getenv('EMAIL_SMTP_SERVER', ''),
    'email_smtp_port': int(os.getenv('EMAIL_SMTP_PORT', '587')),
    'email_username': os.getenv('EMAIL_USERNAME', ''),
    'email_password': os.getenv('EMAIL_PASSWORD', ''),
    'email_recipient': os.getenv('EMAIL_RECIPIENT', ''),
}

# Metrics Configuration
METRICS_CONFIG = {
    'enabled': os.getenv('METRICS_ENABLED', 'False').lower() == 'true',
    'port': int(os.getenv('METRICS_PORT', '9090')),
} 