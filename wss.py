"""
Polymarket WebSocket Client
Real-time market data streaming using Polymarket WebSocket API

Best Use Cases:
1. Real-time price monitoring and alerts
2. Order book depth tracking for liquidity analysis
3. Trade execution monitoring for volume analysis
4. Arbitrage opportunity detection
5. Market maker bot - auto-respond to order book changes
6. Live trading dashboard updates
7. Spread monitoring for optimal entry/exit points
"""

import json
import time
import threading
import os
import ssl
from typing import Dict, List, Optional, Callable, Set
from dataclasses import dataclass
from datetime import datetime
import websocket
from collections import defaultdict

# Try importing ClobClient for API key derivation
try:
    from py_clob_client.client import ClobClient
    CLOB_CLIENT_AVAILABLE = True
except ImportError:
    CLOB_CLIENT_AVAILABLE = False
    print("⚠️  py_clob_client not available. Install it to use automatic API key derivation.")


@dataclass
class PriceAlert:
    """Configuration for a price alert"""
    token_id: str
    condition: str  # "above", "below", "equals"
    target_price: float
    callback: Optional[Callable] = None
    triggered: bool = False


@dataclass
class MarketSnapshot:
    """Snapshot of current market state"""
    token_id: str
    best_bid: float
    best_ask: float
    spread: float
    last_trade_price: Optional[float] = None
    last_trade_time: Optional[datetime] = None
    volume_24h: float = 0.0


class PolymarketWebSocketClient:
    """
    WebSocket client for real-time Polymarket data streaming.
    
    Supports:
    - Subscribing to orderbook updates
    - Tracking best bid/ask changes
    - Monitoring trade executions
    - Price alerts
    - Multi-market tracking
    """
    
    def __init__(self, channel_type: str = "market", ws_url: Optional[str] = None, 
                 api_key: Optional[str] = None, api_secret: Optional[str] = None, 
                 api_passphrase: Optional[str] = None, auto_derive_creds: bool = True):
        """
        Initialize WebSocket client.
        
        Args:
            channel_type: "market" (public orderbook data) or "user" (private user data)
            ws_url: Custom WebSocket URL (optional)
            api_key: Optional API key for authenticated connections (user channel)
            api_secret: Optional API secret for authenticated connections (user channel)
            api_passphrase: Optional API passphrase for authenticated connections (user channel)
            auto_derive_creds: If True, automatically derive API credentials from env vars (PRIV_KEY, FUNDER_ADDRESS)
        """
        # Base URL according to Polymarket docs
        base_url = ws_url or "wss://ws-subscriptions-clob.polymarket.com"
        
        # Construct full URL: base_url + "/ws/" + channel_type
        # Per docs: url + "/ws/" + channel_type where channel_type is "market" or "user"
        self.ws_url = f"{base_url}/ws/{channel_type}"
        self.channel_type = channel_type
        
        self.ws: Optional[websocket.WebSocketApp] = None
        self.is_connected = False
        self.is_running = False
        
        # Track pending subscription (for on_open callback)
        self.pending_subscription: Optional[Dict] = None
        
        # Auto-derive API credentials from environment if available
        if auto_derive_creds and CLOB_CLIENT_AVAILABLE and channel_type == "user":
            self._derive_api_credentials_from_env()
        
        # Authentication (for user channel)
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        
        # Ping thread for keeping connection alive
        self.ping_thread: Optional[threading.Thread] = None
        self.ping_running = False
        self.use_auth = bool(api_key and api_secret and api_passphrase) and channel_type == "user"
        
        # State tracking
        self.subscribed_markets: Set[str] = set()
        self.subscribed_asset_ids: Set[str] = set()  # For Polymarket asset IDs
        self.market_snapshots: Dict[str, MarketSnapshot] = {}
        self.price_alerts: List[PriceAlert] = []
        self.order_book_depth: Dict[str, Dict] = defaultdict(dict)
        self.recent_trades: Dict[str, List[Dict]] = defaultdict(list)
        
        # Callbacks
        self.on_price_update: Optional[Callable] = None
        self.on_trade: Optional[Callable] = None
        self.on_orderbook_update: Optional[Callable] = None
        # New: last trade/price callback (token_id, price, side)
        self.on_last_price_update: Optional[Callable] = None
        
        # Statistics
        self.message_count = 0
        self.last_message_time: Optional[datetime] = None
    
    def _derive_api_credentials_from_env(self):
        """Derive API credentials from environment variables using ClobClient"""
        try:
            priv_key = os.getenv("PRIV_KEY")
            funder_address = os.getenv("FUNDER_ADDRESS")
            
            if not priv_key or not funder_address:
                print("⚠️  PRIV_KEY or FUNDER_ADDRESS not found in environment")
                return
            
            host = "https://clob.polymarket.com"
            chain_id = 137
            
            # Create ClobClient to derive API credentials
            # Try signature_type 1 first (Email/Magic), then 2 (Browser Wallet)
            for sig_type in [1, 2]:
                try:
                    client = ClobClient(
                        host, 
                        key=priv_key, 
                        chain_id=chain_id, 
                        signature_type=sig_type, 
                        funder=funder_address
                    )
                    # Create/derive API credentials
                    api_creds = client.create_or_derive_api_creds()
                    client.set_api_creds(api_creds)
                    
                    # Extract credentials from the returned object
                    # The credentials object should have apiKey, apiSecret, apiPassphrase
                    if api_creds:
                        # Handle different credential formats
                        if isinstance(api_creds, dict):
                            api_creds_dict = api_creds
                        elif hasattr(api_creds, '__dict__'):
                            api_creds_dict = api_creds.__dict__
                        else:
                            # Try accessing as attributes
                            api_creds_dict = {
                                "apiKey": getattr(api_creds, "apiKey", None) or getattr(api_creds, "api_key", None),
                                "apiSecret": getattr(api_creds, "apiSecret", None) or getattr(api_creds, "api_secret", None),
                                "apiPassphrase": getattr(api_creds, "apiPassphrase", None) or getattr(api_creds, "api_passphrase", None)
                            }
                        
                        self.api_key = api_creds_dict.get("apiKey") or api_creds_dict.get("api_key")
                        self.api_secret = api_creds_dict.get("apiSecret") or api_creds_dict.get("api_secret")
                        self.api_passphrase = api_creds_dict.get("apiPassphrase") or api_creds_dict.get("api_passphrase")
                        
                        if self.api_key and self.api_secret and self.api_passphrase:
                            self.use_auth = True
                            print(f"✅ Derived API credentials from environment variables")
                            return
                except Exception as e:
                    continue
            
            print("⚠️  Could not derive API credentials. Using provided credentials or public channel.")
        except Exception as e:
            print(f"⚠️  Error deriving API credentials: {e}")
    
    def connect(self) -> bool:
        """Establish WebSocket connection"""
        if not self.ws_url:
            print("❌ WebSocket URL is not set")
            return False
        
        try:
            print(f"🔌 Connecting to {self.ws_url}...")
            
            # Configure SSL context to handle certificate verification
            # For production, you might want to verify certificates properly
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            self.ws = websocket.WebSocketApp(
                self.ws_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close
            )
            
            # Run WebSocket in a separate thread with SSL context
            self.is_running = True
            ws_thread = threading.Thread(target=self._run_websocket, args=(ssl_context,), daemon=True)
            ws_thread.start()
            
            # Wait for connection
            timeout = 10
            start_time = time.time()
            while not self.is_connected and (time.time() - start_time) < timeout:
                time.sleep(0.1)
            
            if not self.is_connected:
                print(f"⚠️  Connection timeout or failed.")
                if self.channel_type == "user" and not self.use_auth:
                    print(f"   User channel requires authentication.")
                    print(f"   Provide API credentials or ensure PRIV_KEY and FUNDER_ADDRESS env vars are set.")
            
            return self.is_connected
        except Exception as e:
            print(f"❌ Error connecting to WebSocket: {e}")
            print(f"   URL: {self.ws_url}")
            print(f"   Channel: {self.channel_type}")
            return False
    
    def _run_websocket(self, ssl_context=None):
        """Run WebSocket in blocking mode"""
        # Configure SSL if provided
        if ssl_context:
            self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False})
        else:
            self.ws.run_forever()
    
    def _on_open(self, ws):
        """Handle WebSocket connection open"""
        print(f"✅ WebSocket connected to {self.ws_url}")
        self.is_connected = True
        self.ping_running = True
        
        # For user channel, send authentication + subscription
        if self.channel_type == "user" and self.use_auth:
            # Per Polymarket docs: user channel requires auth in subscription
            subscription = {
                "type": "user",
                "auth": {
                    "apiKey": self.api_key,
                    "secret": self.api_secret,
                    "passphrase": self.api_passphrase
                }
            }
            subscription_msg = json.dumps(subscription)
            ws.send(subscription_msg)
            print(f"📡 User channel subscription sent with authentication")
        # Send pending subscription if we have one (per Polymarket docs, subscription happens in on_open)
        elif self.pending_subscription:
            subscription_msg = json.dumps(self.pending_subscription)
            ws.send(subscription_msg)
            print(f"📡 Subscription sent in on_open: {self.pending_subscription.get('type')} channel")
            self.pending_subscription = None
        
        # Start ping thread to keep connection alive (per Polymarket docs)
        self.ping_thread = threading.Thread(target=self._ping_loop, args=(ws,), daemon=True)
        self.ping_thread.start()
    
    def _on_message(self, ws, message: str):
        """Handle incoming WebSocket messages"""
        try:
            # Handle ping/pong raw frames before JSON parsing
            if message == "PING" or message == "PONG":
                return
            data = json.loads(message)
            self.message_count += 1
            self.last_message_time = datetime.now()
            
            # Debug: Log first few messages to understand format
            if self.message_count <= 5:
                print(f"\n🔍 DEBUG - WebSocket message #{self.message_count}:")
                print(f"   Type: {type(data).__name__}")
                print(f"   Data keys: {list(data.keys()) if isinstance(data, dict) else 'N/A (not a dict)'}")
                print(f"   Data: {json.dumps(data, indent=2)[:800]}")
            
            # Handle PONG response to PING
            if isinstance(data, str) and data == "PONG":
                # Pong received, connection is alive
                return
            
            # Handle empty array response (subscription confirmation per Polymarket docs)
            if isinstance(data, list):
                if len(data) == 0:
                    if self.message_count <= 3:
                        print(f"   ✅ Received empty array - subscription confirmation (per Polymarket docs)")
                else:
                    # Process array of messages (could contain multiple updates)
                    for item in data:
                        if isinstance(item, dict):
                            if "price_changes" in item:
                                self._handle_price_changes(item)
                            elif "type" in item:
                                self._handle_message_by_type(item)
                            else:
                                # Could be an orderbook update without explicit type
                                self._handle_orderbook_update(item)
                return
            
            # Handle Polymarket price_changes format (market channel)
            if isinstance(data, dict):
                if "price_changes" in data:
                    self._handle_price_changes(data)
                # Handle different message types based on Polymarket format
                elif "type" in data:
                    self._handle_message_by_type(data)
                else:
                    # Handle messages without explicit type field
                    self._handle_other_message(data)
            
        except json.JSONDecodeError as e:
            print(f"❌ Error parsing message: {e}")
            print(f"Raw message: {message[:200]}")
        except Exception as e:
            print(f"❌ Error handling message: {e}")
            import traceback
            traceback.print_exc()
    
    def _handle_message_by_type(self, data: Dict):
        """Handle messages that have a 'type' field"""
        msg_type = data["type"]
        
        if msg_type == "orderbook" or msg_type == "market":
            self._handle_orderbook_update(data)
        elif msg_type == "trade":
            self._handle_trade(data)
        elif msg_type == "orderbook_snapshot":
            self._handle_orderbook_snapshot(data)
        elif msg_type == "subscription_success" or msg_type == "subscribed":
            print(f"✅ Successfully subscribed to WebSocket channel")
        elif msg_type == "error":
            print(f"❌ WebSocket error: {data.get('message', 'Unknown error')}")
        else:
            # Handle other message types
            self._handle_other_message(data)
            
        # Check price alerts
        self._check_price_alerts()
    
    def _handle_price_changes(self, data: Dict):
        """
        Handle Polymarket price_changes message format.
        Format: {"market": "...", "price_changes": [{"asset_id": "...", ...}]}
        """
        market = data.get("market", "unknown")
        price_changes = data.get("price_changes", [])
        
        if not price_changes:
            return
        
        for price_change in price_changes:
            asset_id = price_change.get("asset_id")
            if not asset_id:
                continue
            
            # Extract bid/ask prices from price_change
            # Polymarket format may vary, try different fields
            best_bid = None
            best_ask = None
            
            # Debug: print first few messages to understand structure
            if self.message_count <= 3:
                print(f"\n🔍 DEBUG [Message #{self.message_count}] - Full price_change structure:")
                print(json.dumps(price_change, indent=2))
            
            # Try to extract prices from various possible fields
            # Check for direct bid/ask fields
            for field in ["bid", "best_bid", "buy_price", "buyPrice", "bestBuy", "best_buy"]:
                if field in price_change and price_change[field] is not None:
                    try:
                        best_bid = float(price_change[field])
                        break
                    except (ValueError, TypeError):
                        continue
            
            for field in ["ask", "best_ask", "sell_price", "sellPrice", "bestSell", "best_sell"]:
                if field in price_change and price_change[field] is not None:
                    try:
                        best_ask = float(price_change[field])
                        break
                    except (ValueError, TypeError):
                        continue
            
            # Last trade price: do NOT treat last trade as best bid/ask; emit via callback and snapshot only
            if "price" in price_change:
                try:
                    side = str(price_change.get("side", "")).lower()
                    price_val = float(price_change["price"])
                except Exception:
                    side = ""
                    price_val = None
                if price_val is not None:
                    # Emit last price callback if available
                    if self.on_last_price_update:
                        try:
                            self.on_last_price_update(asset_id, price_val, side)
                        except Exception:
                            pass
                    # Update snapshot last trade info (if we have a snapshot)
                    snap = self.market_snapshots.get(asset_id)
                    if snap:
                        snap.last_trade_price = price_val
                        try:
                            ts_raw = price_change.get("timestamp") or price_change.get("ts")
                            snap.last_trade_time = datetime.fromtimestamp(int(ts_raw) / 1000) if ts_raw else datetime.now()
                        except Exception:
                            snap.last_trade_time = datetime.now()
            
            # Check for orderbook arrays
            if "bids" in price_change and isinstance(price_change["bids"], list) and price_change["bids"]:
                best_bid = float(price_change["bids"][0].get("price", price_change["bids"][0]))
            if "asks" in price_change and isinstance(price_change["asks"], list) and price_change["asks"]:
                best_ask = float(price_change["asks"][0].get("price", price_change["asks"][0]))
            
            # Calculate spread
            spread = 0
            spread_pct = 0
            mid_price = 0
            
            if best_bid and best_ask:
                spread = best_ask - best_bid
                mid_price = (best_bid + best_ask) / 2
                spread_pct = (spread / best_ask * 100) if best_ask > 0 else 0
            elif best_bid:
                best_ask = best_bid  # Fallback
            elif best_ask:
                best_bid = best_ask  # Fallback
            
            # Update snapshot
            snapshot = self.market_snapshots.get(asset_id)
            if snapshot:
                if best_bid:
                    snapshot.best_bid = best_bid
                if best_ask:
                    snapshot.best_ask = best_ask
                if best_bid and best_ask:
                    snapshot.spread = spread
            else:
                self.market_snapshots[asset_id] = MarketSnapshot(
                    token_id=asset_id,
                    best_bid=best_bid or 0,
                    best_ask=best_ask or 0,
                    spread=spread
                )
            
            # Only display verbose output if no callback is set (callbacks handle their own logging)
            if not self.on_price_update:
                # Display trading-friendly information
                timestamp = datetime.now().strftime("%H:%M:%S")
                
                print(f"\n{'='*80}")
                print(f"📊 PRICE UPDATE [{timestamp}]")
                print(f"{'='*80}")
                print(f"Asset ID: {asset_id[:60]}...")
                print(f"Market:   {market[:60]}...")
                
                if best_bid and best_ask:
                    print(f"\n💰 PRICES:")
                    print(f"   Best Bid:  ${best_bid:.6f} ({best_bid*100:.4f}%)")
                    print(f"   Best Ask:  ${best_ask:.6f} ({best_ask*100:.4f}%)")
                    print(f"   Mid Price: ${mid_price:.6f} ({mid_price*100:.4f}%)")
                    print(f"\n📏 SPREAD:")
                    print(f"   Absolute:  ${spread:.6f}")
                    print(f"   Percentage: {spread_pct:.4f}%")
                    
                    # Trading insights
                    if spread_pct < 0.5:
                        print(f"   ⚡ TIGHT SPREAD - Good entry/exit opportunity!")
                    elif spread_pct > 2.0:
                        print(f"   ⚠️  WIDE SPREAD - High slippage risk")
                elif best_bid:
                    print(f"\n💰 Best Bid: ${best_bid:.6f} ({best_bid*100:.4f}%)")
                    print(f"   ⚠️  No ask price available")
                elif best_ask:
                    print(f"\n💰 Best Ask: ${best_ask:.6f} ({best_ask*100:.4f}%)")
                    print(f"   ⚠️  No bid price available")
            
            # Always call price update callback if available and we have prices
            if self.on_price_update:
                if best_bid and best_ask:
                    self.on_price_update(asset_id, best_bid, best_ask, spread)
                elif best_bid:
                    self.on_price_update(asset_id, best_bid, best_bid, 0)  # Use bid as fallback
                elif best_ask:
                    self.on_price_update(asset_id, best_ask, best_ask, 0)  # Use ask as fallback
            
            # If no prices found and no callback, try REST API fallback
            if not best_bid and not best_ask:
                if not self.on_price_update:  # Only show warnings if no callback
                    print(f"\n⚠️  Price data not found in WebSocket message")
                    print(f"Available fields: {list(price_change.keys())}")
                
                # Try to fetch prices from REST API as fallback
                try:
                    from getEvents import get_current_price
                    print(f"\n🔄 Attempting to fetch prices from REST API...")
                    fetched_bid = get_current_price(asset_id, "BUY")
                    fetched_ask = get_current_price(asset_id, "SELL")
                    
                    if fetched_bid and fetched_ask:
                        best_bid = fetched_bid
                        best_ask = fetched_ask
                        spread = best_ask - best_bid
                        mid_price = (best_bid + best_ask) / 2
                        spread_pct = (spread / best_ask * 100) if best_ask > 0 else 0
                        
                        print(f"\n💰 PRICES (from REST API):")
                        print(f"   Best Bid:  ${best_bid:.6f} ({best_bid*100:.4f}%)")
                        print(f"   Best Ask:  ${best_ask:.6f} ({best_ask*100:.4f}%)")
                        print(f"   Mid Price: ${mid_price:.6f} ({mid_price*100:.4f}%)")
                        print(f"\n📏 SPREAD:")
                        print(f"   Absolute:  ${spread:.6f}")
                        print(f"   Percentage: {spread_pct:.4f}%")
                        
                        # Update snapshot
                        snapshot = self.market_snapshots.get(asset_id)
                        if snapshot:
                            snapshot.best_bid = best_bid
                            snapshot.best_ask = best_ask
                            snapshot.spread = spread
                        else:
                            self.market_snapshots[asset_id] = MarketSnapshot(
                                token_id=asset_id,
                                best_bid=best_bid,
                                best_ask=best_ask,
                                spread=spread
                            )
                        
                        # Call callback
                        if self.on_price_update:
                            self.on_price_update(asset_id, best_bid, best_ask, spread)
                    else:
                        if not self.on_price_update:  # Only print if no callback
                            print(f"   ❌ Could not fetch prices from REST API")
                except ImportError:
                    print(f"   ℹ️  Install getEvents.py to enable REST API price fallback")
                except Exception as e:
                    print(f"   ❌ Error fetching prices: {e}")
                
                # Show raw data for debugging (only if no callback)
                if self.message_count <= 5 and not self.on_price_update:
                    print(f"\n📋 Raw price_change data:")
                    print(json.dumps(price_change, indent=2)[:500])
            
            # Only print separator if no callback (callbacks handle their own formatting)
            if not self.on_price_update:
                print(f"{'='*80}")
    
    def _on_error(self, ws, error):
        """Handle WebSocket errors"""
        error_str = str(error)
        if "nodename nor servname" in error_str or "Name or service not known" in error_str:
            print(f"❌ DNS resolution failed for {self.ws_url}")
            print(f"   This usually means the WebSocket URL is incorrect or the service is unavailable.")
            print(f"   Check the Polymarket documentation for the correct WebSocket endpoint.")
        else:
            print(f"❌ WebSocket error: {error}")
    
    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection close"""
        print(f"🔌 WebSocket closed (code: {close_status_code}, msg: {close_msg})")
        self.is_connected = False
        self.ping_running = False  # Stop ping thread
        
        # Only reconnect market channel automatically, not user channel
        # User channel disconnects are often intentional or auth-related
        if self.is_running and self.channel_type == "market":
            print("🔄 Attempting to reconnect...")
            time.sleep(2)
            self.connect()
        elif self.channel_type == "user":
            # Don't auto-reconnect user channel to avoid spam
            pass
    
    def _ping_loop(self, ws):
        """Send PING messages every 30 seconds to keep connection alive (per Polymarket docs)"""
        while self.ping_running and self.is_connected:
            try:
                time.sleep(30)  # Polymarket recommends PING every 30 seconds
                if self.is_connected and ws:
                    ws.send("PING")
            except Exception as e:
                # Connection closed, stop pinging silently
                break
    
    def subscribe_orderbook(self, asset_ids: List[str], initial_dump: bool = True):
        """
        Subscribe to orderbook updates for specific assets (market channel).
        
        Args:
            asset_ids: List of asset IDs to subscribe to (e.g., ["0x123...", "0x456..."])
            initial_dump: If True, receive initial orderbook state (default: True per Polymarket docs)
        """
        if self.channel_type != "market":
            print(f"❌ Can only subscribe to orderbook on 'market' channel. Current channel: {self.channel_type}")
            return
        
        if not asset_ids:
            print("❌ No asset IDs provided for subscription")
            return
        
        # Per Polymarket docs: market channel format is {"assets_ids": [...], "type": "market"}
        # Also include initial_dump per May 28, 2025 changelog
        subscription = {
            "type": "market",
            "assets_ids": asset_ids,
            "initial_dump": initial_dump
        }
        
        # Log the subscription we're sending
        print(f"\n🔍 WebSocket Subscription Details:")
        print(f"   Type: market")
        print(f"   Asset IDs count: {len(asset_ids)}")
        print(f"   Initial dump: {initial_dump}")
        print(f"   Subscription payload: {json.dumps(subscription, indent=2)}")
        
        self.subscribed_asset_ids.update(asset_ids)
        
        # Per Polymarket docs, subscription should be sent in on_open
        # If already connected, send immediately; otherwise queue for on_open
        if self.is_connected and self.ws:
            self._send(subscription)
            print(f"📡 Subscription sent for {len(asset_ids)} assets")
        else:
            # Queue subscription for on_open
            self.pending_subscription = subscription
            print(f"📡 Subscription queued (will send on connection open) for {len(asset_ids)} assets")
    
    def subscribe_user_channel(self, markets: List[str]):
        """
        Subscribe to user channel for specific markets (requires authentication).
        
        Args:
            markets: List of market IDs to subscribe to
        """
        if not self.is_connected:
            print("❌ Not connected to WebSocket. Call connect() first.")
            return
        
        if self.channel_type != "user":
            print(f"❌ Can only subscribe to user channel on 'user' channel. Current channel: {self.channel_type}")
            return
        
        if not self.use_auth:
            print("❌ User channel requires authentication. Provide API credentials.")
            return
        
        # Per Polymarket docs: user channel format
        subscription = {
            "type": "user",
            "markets": markets
        }
        
        if self.api_key and self.api_secret and self.api_passphrase:
            subscription["auth"] = {
                "apiKey": self.api_key,
                "secret": self.api_secret,
                "passphrase": self.api_passphrase
            }
        
        self.subscribed_markets.update(markets)
        self._send(subscription)
        print(f"📡 Subscribed to user channel for markets: {markets}")
    
    def subscribe_trades(self, asset_ids: List[str]):
        """
        Subscribe to trade updates for specific assets (market channel).
        
        Args:
            asset_ids: List of asset IDs to subscribe to
        """
        if not self.is_connected:
            print("❌ Not connected to WebSocket. Call connect() first.")
            return
        
        if self.channel_type != "market":
            print(f"❌ Can only subscribe to trades on 'market' channel. Current channel: {self.channel_type}")
            return
        
        # Trades are part of the market channel subscription
        # The market channel provides both orderbook and trades for the subscribed assets
        subscription = {
            "type": "market",
            "assets_ids": asset_ids
        }
        
        self.subscribed_asset_ids.update(asset_ids)
        self._send(subscription)
        print(f"📡 Subscribed to trades for assets: {asset_ids}")
    
    def subscribe_to_market(self, asset_ids: List[str]):
        """Subscribe to both orderbook and trades for assets (market channel includes both)"""
        self.subscribe_orderbook(asset_ids)
    
    def unsubscribe(self, token_id: str):
        """Unsubscribe from a token"""
        if not self.is_connected:
            return
        
        unsubscribe_msg = {
            "type": "unsubscribe",
            "token_id": token_id
        }
        
        self._send(unsubscribe_msg)
        self.subscribed_markets.discard(token_id)
        print(f"📴 Unsubscribed from token: {token_id}")
    
    def _send(self, message: Dict):
        """Send message through WebSocket"""
        if self.ws and self.is_connected:
            try:
                self.ws.send(json.dumps(message))
            except Exception as e:
                print(f"Error sending message: {e}")
    
    def _handle_orderbook_update(self, data: Dict):
        """Handle orderbook update message"""
        token_id = data.get("token_id")
        if not token_id:
            # Try alternative field names
            token_id = data.get("asset_id") or data.get("tokenId") or data.get("assetId")
        
        if not token_id:
            # Debug: log what we got
            if self.message_count <= 5:
                print(f"\n🔍 DEBUG - Orderbook update missing token_id:")
                print(f"   Available fields: {list(data.keys())}")
                print(f"   Data: {json.dumps(data, indent=2)[:400]}")
            return
        
        # Extract best bid/ask - try multiple field formats
        best_bid = None
        best_ask = None
        
        # Try different field names
        for bid_field in ["best_bid", "bestBid", "bid", "buy", "best_buy"]:
            if bid_field in data:
                try:
                    best_bid = float(data[bid_field])
                    break
                except (ValueError, TypeError):
                    continue
        
        for ask_field in ["best_ask", "bestAsk", "ask", "sell", "best_sell"]:
            if ask_field in data:
                try:
                    best_ask = float(data[ask_field])
                    break
                except (ValueError, TypeError):
                    continue
        
        # If not found in top-level, check bids/asks arrays
        if not best_bid and "bids" in data and isinstance(data["bids"], list) and len(data["bids"]) > 0:
            first_bid = data["bids"][0]
            if isinstance(first_bid, dict):
                best_bid = float(first_bid.get("price", first_bid.get("px", first_bid.get("size", 0))))
            elif isinstance(first_bid, (int, float)):
                best_bid = float(first_bid)
            elif isinstance(first_bid, list) and len(first_bid) >= 2:
                # Format: [price, size]
                best_bid = float(first_bid[0])
        
        if not best_ask and "asks" in data and isinstance(data["asks"], list) and len(data["asks"]) > 0:
            first_ask = data["asks"][0]
            if isinstance(first_ask, dict):
                best_ask = float(first_ask.get("price", first_ask.get("px", first_ask.get("size", 0))))
            elif isinstance(first_ask, (int, float)):
                best_ask = float(first_ask)
            elif isinstance(first_ask, list) and len(first_ask) >= 2:
                # Format: [price, size]
                best_ask = float(first_ask[0])
        
        # Fallback to default if still not found
        if best_bid is None or best_bid == 0:
            best_bid = float(data.get("best_bid", data.get("bid", 0)))
        if best_ask is None or best_ask == 0:
            best_ask = float(data.get("best_ask", data.get("ask", 0)))
        
        spread = best_ask - best_bid if best_ask and best_bid else 0
        
        # Debug log for first few orderbook updates
        if self.message_count <= 5 and (best_bid or best_ask):
            print(f"\n🔍 DEBUG - Orderbook update for {token_id[:40]}...:")
            print(f"   Extracted: bid=${best_bid:.6f}, ask=${best_ask:.6f}")
        
        snapshot = self.market_snapshots.get(token_id)
        if snapshot:
            snapshot.best_bid = best_bid
            snapshot.best_ask = best_ask
            snapshot.spread = spread
        else:
            self.market_snapshots[token_id] = MarketSnapshot(
                token_id=token_id,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread
            )
        
        # Store orderbook data
        if "bids" in data:
            self.order_book_depth[token_id]["bids"] = data["bids"]
        if "asks" in data:
            self.order_book_depth[token_id]["asks"] = data["asks"]
        
        # Call callback if set
        if self.on_orderbook_update:
            self.on_orderbook_update(token_id, data)
        
        # Call price update callback
        if self.on_price_update:
            self.on_price_update(token_id, best_bid, best_ask, spread)
    
    def _handle_orderbook_snapshot(self, data: Dict):
        """Handle initial orderbook snapshot"""
        self._handle_orderbook_update(data)
    
    def _handle_trade(self, data: Dict):
        """Handle trade execution message"""
        token_id = data.get("token_id")
        if not token_id:
            return
        
        trade_info = {
            "price": float(data.get("price", 0)),
            "size": float(data.get("size", 0)),
            "side": data.get("side", "unknown"),
            "timestamp": datetime.now(),
            "maker": data.get("maker"),
            "taker": data.get("taker")
        }
        
        # Store recent trades (keep last 100)
        self.recent_trades[token_id].append(trade_info)
        if len(self.recent_trades[token_id]) > 100:
            self.recent_trades[token_id].pop(0)
        
        # Update market snapshot
        snapshot = self.market_snapshots.get(token_id)
        if snapshot:
            snapshot.last_trade_price = trade_info["price"]
            snapshot.last_trade_time = trade_info["timestamp"]
            snapshot.volume_24h += trade_info["size"]
        
        # Call callback if set
        if self.on_trade:
            self.on_trade(token_id, trade_info)
        
        # print(f"💱 Trade: {token_id[:20]}... | Price: ${trade_info['price']:.4f} | Size: {trade_info['size']} | Side: {trade_info['side']}")
    
    def _handle_other_message(self, data: Dict):
        """Handle other message types including user channel messages"""
        # Per Polymarket docs: User Channel messages have event_type
        event_type = data.get("event_type")
        
        if event_type in ("trade", "order"):
            # User channel message - forward to callback if registered
            if self.channel_type == "user":
                print(f"\n📨 User Channel Message:")
                print(f"   Event: {event_type}")
                print(f"   Type: {data.get('type')}")
                print(f"   Data: {json.dumps(data, indent=2)[:500]}")
            
            if hasattr(self, 'on_user_message') and callable(self.on_user_message):
                self.on_user_message(data)
            else:
                print(f"   ⚠️  No on_user_message callback registered!")
            return
        
        # Log any other messages from user channel for debugging
        if self.channel_type == "user" and self.message_count <= 10:
            print(f"\n📨 User Channel Other Message:")
            print(f"   Keys: {list(data.keys())}")
            print(f"   Data: {json.dumps(data, indent=2)[:300]}")
        
        # Try to extract any price information from unknown formats
        if "price" in data or "bid" in data or "ask" in data:
            # print(f"📨 Unknown message format with price data: {json.dumps(data, indent=2)[:300]}...")
            pass
        # Otherwise, silently handle or log if needed
    
    def add_price_alert(self, token_id: str, condition: str, target_price: float, 
                       callback: Optional[Callable] = None):
        """
        Add a price alert for a token.
        
        Args:
            token_id: Token to monitor
            condition: "above", "below", or "equals"
            target_price: Price threshold
            callback: Optional function to call when alert triggers
        """
        alert = PriceAlert(
            token_id=token_id,
            condition=condition,
            target_price=target_price,
            callback=callback
        )
        self.price_alerts.append(alert)
        print(f"🔔 Added price alert: {token_id[:20]}... | {condition} ${target_price:.4f}")
    
    def _check_price_alerts(self):
        """Check if any price alerts should trigger"""
        for alert in self.price_alerts:
            if alert.triggered:
                continue
            
            snapshot = self.market_snapshots.get(alert.token_id)
            if not snapshot:
                continue
            
            current_price = snapshot.best_bid if alert.condition == "below" else snapshot.best_ask
            
            should_trigger = False
            if alert.condition == "above" and current_price >= alert.target_price:
                should_trigger = True
            elif alert.condition == "below" and current_price <= alert.target_price:
                should_trigger = True
            elif alert.condition == "equals" and abs(current_price - alert.target_price) < 0.0001:
                should_trigger = True
            
            if should_trigger:
                alert.triggered = True
                message = f"🚨 ALERT: {alert.token_id[:20]}... | Price {alert.condition} ${alert.target_price:.4f} | Current: ${current_price:.4f}"
                print(message)
                
                if alert.callback:
                    alert.callback(alert.token_id, current_price, alert.target_price)
    
    def get_market_snapshot(self, token_id: str) -> Optional[MarketSnapshot]:
        """Get current market snapshot for a token"""
        return self.market_snapshots.get(token_id)
    
    def get_orderbook(self, token_id: str) -> Dict:
        """Get current orderbook for a token"""
        return self.order_book_depth.get(token_id, {})
    
    def get_recent_trades(self, token_id: str, limit: int = 10) -> List[Dict]:
        """Get recent trades for a token"""
        trades = self.recent_trades.get(token_id, [])
        return trades[-limit:] if trades else []
    
    def get_stats(self) -> Dict:
        """Get WebSocket connection statistics"""
        return {
            "connected": self.is_connected,
            "subscribed_markets": len(self.subscribed_markets),
            "message_count": self.message_count,
            "last_message_time": self.last_message_time.isoformat() if self.last_message_time else None,
            "active_alerts": len([a for a in self.price_alerts if not a.triggered]),
            "market_snapshots": len(self.market_snapshots)
        }
    
    def disconnect(self):
        """Disconnect from WebSocket"""
        self.is_running = False
        self.ping_running = False
        if self.ws:
            self.ws.close()
        self.is_connected = False
        print("🔌 Disconnected from WebSocket")


if __name__ == "__main__":
    print("Polymarket WebSocket Client")
    print("="*80)
    print("Examples have been moved to separate files:")
    print("  - example_1_basic_price_monitoring.py")
    print("  - example_2_price_alerts.py")
    print("  - example_3_trade_monitoring.py")
    print("  - example_4_orderbook_depth_tracking.py")
    print("  - example_5_multi_market_monitoring.py")
    print("  - example_6_arbitrage_detection.py")
    print("  - example_7_spread_monitoring.py")
    print("  - example_8_market_maker_bot.py")
    print("  - example_9_arbitrage_only_logging.py")
    print("  - example_integrated_with_getMarkets.py")
    print("\nRun any example file directly with: python <filename>.py")
    print("="*80)

