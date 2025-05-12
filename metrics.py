import logging
from prometheus_client import start_http_server, Counter, Gauge, Histogram
from config import METRICS_CONFIG

logger = logging.getLogger(__name__)

# Define metrics
ORDER_ATTEMPTS = Counter('kraken_bot_order_attempts_total', 'Total number of order attempts')
ORDER_SUCCESS = Counter('kraken_bot_order_success_total', 'Total number of successful orders')
ORDER_FAILURES = Counter('kraken_bot_order_failures_total', 'Total number of failed orders')
ORDER_AMOUNT = Gauge('kraken_bot_order_amount_btc', 'Amount of BTC in the last order')
ORDER_PRICE = Gauge('kraken_bot_order_price_eur', 'Price of the last order in EUR')
ORDER_LATENCY = Histogram('kraken_bot_order_latency_seconds', 'Time taken to place and fill orders')
BALANCE_EUR = Gauge('kraken_bot_balance_eur', 'Current EUR balance')
BALANCE_BTC = Gauge('kraken_bot_balance_btc', 'Current BTC balance')

class MetricsManager:
    def __init__(self):
        self.enabled = METRICS_CONFIG['enabled']
        if self.enabled:
            try:
                start_http_server(METRICS_CONFIG['port'])
                logger.info(f"Metrics server started on port {METRICS_CONFIG['port']}")
            except Exception as e:
                logger.error(f"Failed to start metrics server: {e}")
                self.enabled = False

    def record_order_attempt(self):
        if self.enabled:
            ORDER_ATTEMPTS.inc()

    def record_order_success(self, amount_btc, price_eur, latency_seconds):
        if self.enabled:
            ORDER_SUCCESS.inc()
            ORDER_AMOUNT.set(amount_btc)
            ORDER_PRICE.set(price_eur)
            ORDER_LATENCY.observe(latency_seconds)

    def record_order_failure(self):
        if self.enabled:
            ORDER_FAILURES.inc()

    def update_balances(self, eur_balance, btc_balance):
        if self.enabled:
            BALANCE_EUR.set(eur_balance)
            BALANCE_BTC.set(btc_balance)

# Create a global metrics manager instance
metrics_manager = MetricsManager() 