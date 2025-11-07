# Polymarket Market Making Bot

A Python-based market-making bot for [Polymarket](https://polymarket.com/) using the CLOB (Centralized Limit Order Book) API with real-time WebSocket price updates.

## Core Features

### PolymarketInterface (`polymarketInterface.py`)

Provides a complete interface to Polymarket's CLOB orderbook using the `py-clob-client` library:

- **Order Management**
  - Place orders asynchronously with automatic signing and submission to CLOB
  - Cancel orders with order ID tracking
  - Track open orders with state management
  - Get current open orders from the API

- **Market Data**
  - Real-time bid/ask prices via WebSocket
  - Current mid-price calculation
  - Spread monitoring
  - Market existence validation

- **Real-Time Updates**
  - Integrated WebSocket client (`wss.py`) for live price feeds
  - Automatic price update callbacks
  - Low-latency market monitoring

- **Authentication**
  - Automatic API credential derivation from private key
  - Support for environment variables (PRIV_KEY, FUNDER_ADDRESS)
  - Secure order signing using Polygon private key

### Market Maker (`MM.py`)

High-level market-making strategy logic:
- Monitors a single market in real-time
- Executes market-making algorithm
- Manages bid/ask spread positioning
- Handles order placement and cancellation logic

### Band Configuration (`Bands.py`)

Strategy parameters and constraints:
- Reads configuration from `bands.json`
- Determines order quantities and spread requirements
- Enforces balance and risk limits
- Manages time-based constraints

### WebSocket Client (`wss.py`)

Real-time Polymarket market data streaming:
- Connects to `wss://ws-subscriptions-clob.polymarket.com`
- Subscribes to orderbook updates for specific assets
- Supports price alerts and callbacks
- Automatic reconnection handling
- Trade execution monitoring

## Getting Started

### Prerequisites

- Python 3.11+
- Private key and funder address for Polymarket account
- Polygon (MATIC) for gas fees

### Installation

```bash
pip install -r pyproject.toml
```

Or manually:

```bash
pip install py-clob-client>=0.28.0 websocket-client web3 python-dotenv requests
```

### Environment Setup

Create a `.env` file with your credentials:

```env
PRIV_KEY=your_private_key_here
FUNDER_ADDRESS=your_funder_address_here
```

Or set environment variables:

```bash
export PRIV_KEY="your_private_key"
export FUNDER_ADDRESS="your_funder_address"
```

### Configuration

Edit `bands.json` to set:
- Target market (FPMM address)
- Asset ID (token outcome)
- Spread requirements
- Order sizes
- Balance limits

### Running the Bot

```bash
python MM.py
```

## Architecture

### Order Flow

1. **Connect** - Initialize CLOB client and WebSocket connection
2. **Monitor** - Receive real-time price updates via WebSocket
3. **Calculate** - Determine optimal bid/ask quotes based on strategy
4. **Execute** - Place orders asynchronously with thread pool
5. **Manage** - Track, update, and cancel orders as needed
6. **Cleanup** - Graceful disconnection and shutdown

### Async Pattern

- ThreadPoolExecutor for concurrent order operations
- Thread-safe state management with locks
- Non-blocking price updates via WebSocket callbacks
- Timeout protection on all async operations

### Real-Time Data Flow

```
Polymarket WebSocket
      ↓
  PolymarketWebSocketClient (wss.py)
      ↓
  Price Update Callback
      ↓
  PolymarketInterface (state update)
      ↓
  Market Maker Strategy
      ↓
  Order Placement via CLOB API
```

## Key Methods

### PolymarketInterface

- `connect_ws()` - Connect to real-time price feed
- `place_orders(orders)` - Place multiple orders asynchronously
- `cancel_orders(order_ids)` - Cancel orders by ID
- `get_orders()` - Fetch current open orders
- `get_price()` - Get current bid/ask/mid prices
- `get_market()` - Validate market exists
- `disconnect()` - Clean shutdown

### Usage Example

```python
# Initialize
interface = PolymarketInterface(
    fpmm_address="0x...",
    asset_id="123456789",
    private_key="0x...",
    funder_address="0x..."
)

# Connect to real-time prices
interface.connect_ws()

# Place orders
orders = [
    Order(size=100, price=0.45, is_buy=True),
    Order(size=100, price=0.55, is_buy=False)
]
interface.place_orders(orders)

# Monitor prices
prices = interface.get_price()
print(f"Bid: ${prices['bid']}, Ask: ${prices['ask']}, Spread: ${prices['spread']}")

# Clean up
interface.disconnect()
```

## Configuration Files

### bands.json

Market-making parameters per market:
- FPMM address
- Asset ID
- Bid/ask spread
- Order sizes
- Balance requirements

### pyproject.toml

Python dependencies and project metadata.

## Error Handling

- Automatic retry logic for API failures
- WebSocket reconnection on disconnect
- Thread-safe error logging
- Graceful degradation with warnings
- Exception tracking and reporting

## Threading Model

- Main thread: Strategy execution and monitoring
- WebSocket thread: Real-time price updates (daemon)
- Executor threads: Async order operations (pool of 5 by default)
- Ping thread: Keep-alive mechanism for WebSocket

## Deployment

The bot is designed for:
- Production market-making on Polymarket
- Single market operation (extensible to multi-market)
- Continuous 24/7 operation
- Minimal external dependencies