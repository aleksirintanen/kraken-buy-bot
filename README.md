# Kraken Auto-Buy Bot

A Python bot that automatically buys Bitcoin on Kraken exchange using a scheduled strategy. The bot attempts to buy BTC every Monday at 2 AM UTC, with a fallback attempt on Sunday at 2 AM UTC if the Monday attempt is unsuccessful.

## Features

- Scheduled buying on Monday 2 AM UTC with Sunday fallback
- Configurable minimum BTC amount
- Dry-run mode for testing without real orders
- Test mode for one-time real purchase with minimum amount
- State persistence across restarts
- Docker support for easy deployment

## Prerequisites

- Python 3.11 or higher
- Kraken API key with trading permissions
- Docker (optional, for containerized deployment)

## Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/kraken-auto-buy-bot.git
cd kraken-auto-buy-bot
```

2. Create a virtual environment and install dependencies:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3. Create a `.env` file with your Kraken API credentials:
```bash
KRAKEN_API_KEY=your_api_key_here
KRAKEN_API_SECRET=your_api_secret_here
```

## Configuration

The bot has three modes that can be configured in `bot.py`:

1. **Normal Mode** (`DRY_RUN = False, TEST_MODE = False`):
   - Runs scheduled trades every Monday at 2 AM UTC
   - Falls back to Sunday at 2 AM UTC if Monday's attempt fails
   - Uses 20% of available EUR balance

2. **Dry Run Mode** (`DRY_RUN = True`):
   - Simulates trading without placing real orders
   - Checks real market prices to simulate success/failure
   - Runs once and exits

3. **Test Mode** (`TEST_MODE = True`):
   - Makes one real purchase with minimum BTC amount (0.00005 BTC)
   - Useful for testing API connectivity and order placement
   - Exits after successful purchase or max retries

## Usage

### Running Locally

1. Activate the virtual environment:
```bash
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Run the bot:
```bash
python bot.py
```

### Running with Docker

1. Build and start the container:
```bash
docker-compose up -d
```

2. View logs:
```bash
docker-compose logs -f
```

3. Stop the bot:
```bash
docker-compose down
```

## State Management

The bot maintains its state in `bot_state.json` to track whether Monday's attempt was successful. This state persists across restarts and is used to determine if the Sunday fallback should run.

## Safety Features

- Minimum BTC amount check (0.00005 BTC)
- Dry-run mode for testing
- Test mode for small real purchases
- State persistence to prevent duplicate orders
- 5-minute timeout for unfilled orders


## Disclaimer

This bot is for educational purposes only. Use at your own risk. Cryptocurrency trading involves significant risk and you should never invest more than you can afford to lose. 