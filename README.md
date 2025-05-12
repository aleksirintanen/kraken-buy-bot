# Kraken Buy Bot

A Python bot that automatically places limit buy orders for Bitcoin on Kraken exchange. The bot runs on a schedule, attempting to buy BTC on Monday mornings with a fallback to Sunday if the Monday attempt fails.

## Features

- Scheduled buying on Monday 02:00 UTC with Sunday fallback
- Configurable dry run mode for testing
- Test mode for one-time real purchases with minimum amount
- Persistent state tracking between runs
- Docker support for easy deployment

## Prerequisites

- Python 3.8 or higher
- Docker and Docker Compose (for containerized deployment)
- Kraken API credentials with trading permissions

## Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/kraken-buy-bot.git
cd kraken-buy-bot
```

2. Create a `.env` file:
```bash
cp .env.example .env
```

3. Edit `.env` and add your Kraken API credentials:
```
KRAKEN_API_KEY=your_api_key_here
KRAKEN_API_SECRET=your_api_secret_here
DRY_RUN=False
TEST_MODE=False
TZ=UTC
```

## Running with Docker

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

## Running Locally

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the bot:
```bash
python bot.py
```

## Configuration

- `DRY_RUN`: Set to `True` to simulate trades without actually placing orders
- `TEST_MODE`: Set to `True` for a one-time real purchase with minimum amount
- `TZ`: Timezone for the bot (defaults to UTC)

## State Management

The bot maintains its state in `bot_state.json`, which tracks whether the Monday attempt was successful. This file is persisted between runs when using Docker.

## Logging

Logs are printed to stdout and can be viewed using Docker logs or redirected to a file when running locally.

## Security Notes

- Never commit your `.env` file or expose your API credentials
- The bot uses 20% of your EUR balance for each purchase (configurable in the code)
- Always test with `DRY_RUN=True` first
- Consider using API keys with trading-only permissions

## License

MIT License 