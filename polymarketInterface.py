"""
Interfaces with Polymarket's CLOB orderbook via py-clob-client.
Handles all interactions with Polymarket's orderbook:
- Placing and cancelling orders
- Checking if a market exists
- Getting market prices and spreads
- Managing open orders
- Real-time WebSocket price updates
"""

import logging
import threading
import os
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    CLOB_CLIENT_AVAILABLE = True
except ImportError:
    CLOB_CLIENT_AVAILABLE = False
    print("⚠️  py_clob_client not available. Install with: pip install py-clob-client")

from wss import PolymarketWebSocketClient


class Order:
    """Represents a single order"""
    def __init__(self, size: float, price: float, is_buy: bool) -> None:
        self.size = size
        self.price = price
        self.is_buy = is_buy
        self.id: Optional[str] = None
        self.created_at = datetime.now()
        self.status = "pending"  # pending, active, cancelled, filled


class PolymarketInterface:
    """Interface to Polymarket's CLOB orderbook for market making"""
    
    def __init__(
        self, 
        fpmm_address: str, 
        asset_id: str, 
        refresh_frequency: int = 1,
        max_workers: int = 5,
        private_key: Optional[str] = None,
        funder_address: Optional[str] = None
    ) -> None:
        """
        Initializes interface to Polymarket's CLOB orderbook.
        
        Args:
            fpmm_address: The FPMM (Fixed Product Market Maker) address for the market
            asset_id: The token ID/asset ID for the outcome being traded
            refresh_frequency: How often to refresh prices (in seconds)
            max_workers: Number of worker threads for async operations
            private_key: Private key for signing orders (or set PRIV_KEY env var)
            funder_address: Funder address for the account (or set FUNDER_ADDRESS env var)
        """
        if not CLOB_CLIENT_AVAILABLE:
            raise ImportError("py_clob_client is required. Install with: pip install py-clob-client")
        
        assert isinstance(fpmm_address, str)
        assert isinstance(asset_id, str)
        assert isinstance(refresh_frequency, int)

        self.fpmm_address = fpmm_address
        self.asset_id = asset_id
        self.refresh_frequency = refresh_frequency

        # Setup CLOB client for order management
        self.private_key = private_key or os.getenv("PRIV_KEY")
        self.funder_address = funder_address or os.getenv("FUNDER_ADDRESS")
        
        if not self.private_key or not self.funder_address:
            raise ValueError(
                "Must provide private_key and funder_address either as arguments or via "
                "PRIV_KEY and FUNDER_ADDRESS environment variables"
            )
        
        # Initialize CLOB client
        self.clob_client = ClobClient(
            host="https://clob.polymarket.com",
            key=self.private_key,
            chain_id=137,  # Polygon mainnet
            signature_type=2,  # Browser wallet signature type
            funder=self.funder_address
        )
        
        # Derive/create API credentials
        try:
            api_creds = self.clob_client.create_or_derive_api_creds()
            self.clob_client.set_api_creds(api_creds)
            print(f"✅ Initialized Polymarket CLOB client for {self.fpmm_address[:20]}...")
        except Exception as e:
            print(f"⚠️  Error deriving API credentials: {e}")
            raise

        # Concurrent execution
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()

        # State management
        self.open_orders: Dict[str, Order] = {}  # order_id -> Order
        self.current_mid_price: Optional[float] = None
        self.current_best_bid: Optional[float] = None
        self.current_best_ask: Optional[float] = None
        
        # WebSocket client for real-time price updates
        self.ws_client: Optional[PolymarketWebSocketClient] = None


    def connect_ws(self) -> bool:
        """
        Connect to Polymarket WebSocket for real-time price updates.
        
        Returns:
            True if connected successfully, False otherwise
        """
        try:
            self.ws_client = PolymarketWebSocketClient(
                channel_type="market",
                api_key=self.clob_client.api_key if hasattr(self.clob_client, 'api_key') else None,
                api_secret=self.clob_client.api_secret if hasattr(self.clob_client, 'api_secret') else None,
                api_passphrase=self.clob_client.api_passphrase if hasattr(self.clob_client, 'api_passphrase') else None,
                auto_derive_creds=False
            )
            
            # Set up price update callback
            self.ws_client.on_price_update = self._on_price_update
            
            # Connect and subscribe
            if self.ws_client.connect():
                self.ws_client.subscribe_orderbook([self.asset_id], initial_dump=True)
                return True
            return False
        except Exception as e:
            logging.error(f"Failed to connect WebSocket: {e}")
            return False

    def _on_price_update(self, token_id: str, best_bid: float, best_ask: float, spread: float):
        """Callback for WebSocket price updates"""
        with self._lock:
            if token_id == self.asset_id:
                self.current_best_bid = best_bid
                self.current_best_ask = best_ask
                self.current_mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else None

    def place_orders(self, new_orders: List[Order]) -> bool:
        """
        Places a list of Order objects on the orderbook asynchronously.
        
        Args:
            new_orders: List of Order objects to place
            
        Returns:
            True if all orders placed successfully
        """
        assert isinstance(new_orders, list)
        
        success = True
        for order in new_orders:
            future = self._executor.submit(self._place_order_async, order)
            try:
                order_id = future.result(timeout=30)
                if order_id:
                    order.id = order_id
                    order.status = "active"
                    with self._lock:
                        self.open_orders[order_id] = order
                    logging.info(f"✅ Placed order {order_id}: {'BUY' if order.is_buy else 'SELL'} {order.size} @ ${order.price}")
                else:
                    order.status = "failed"
                    success = False
                    logging.warning(f"❌ Failed to place order: {'BUY' if order.is_buy else 'SELL'} {order.size} @ ${order.price}")
            except Exception as e:
                order.status = "failed"
                success = False
                logging.error(f"❌ Exception placing order: {e}")
        
        return success

    def cancel_orders(self, order_ids: List[str]) -> bool:
        """
        Cancels a list of orders asynchronously.
        
        Args:
            order_ids: List of order IDs to cancel
            
        Returns:
            True if all cancellations requested successfully
        """
        assert isinstance(order_ids, list)
        
        success = True
        for order_id in order_ids:
            future = self._executor.submit(self._cancel_order_async, order_id)
            try:
                if future.result(timeout=30):
                    with self._lock:
                        if order_id in self.open_orders:
                            self.open_orders[order_id].status = "cancelled"
                            del self.open_orders[order_id]
                    logging.info(f"✅ Cancelled order {order_id}")
                else:
                    success = False
                    logging.warning(f"❌ Failed to cancel order {order_id}")
            except Exception as e:
                success = False
                logging.error(f"❌ Exception cancelling order {order_id}: {e}")
        
        return success

    def get_orders(self) -> List[Order]:
        """
        Gets the user's current open orders.
        
        Returns:
            List of open Order objects
        """
        try:
            # Query open orders from CLOB API
            api_orders = self.clob_client.get_orders(market=self.fpmm_address)
            
            with self._lock:
                # Update our local tracking
                self.open_orders.clear()
                for api_order in api_orders:
                    order_id = api_order.get("id")
                    if order_id:
                        order = Order(
                            size=float(api_order.get("size", 0)),
                            price=float(api_order.get("price", 0)),
                            is_buy=api_order.get("side", "").lower() == "buy"
                        )
                        order.id = order_id
                        order.status = "active"
                        self.open_orders[order_id] = order
                
                return list(self.open_orders.values())
        except Exception as e:
            logging.error(f"Error fetching orders: {e}")
            return list(self.open_orders.values())

    def get_price(self) -> Dict[str, float]:
        """
        Gets the current best bid, ask, and mid price.
        
        Returns:
            Dictionary with 'bid', 'ask', 'mid', and 'spread' keys
        """
        with self._lock:
            if self.current_best_bid is None or self.current_best_ask is None:
                return {
                    "bid": None,
                    "ask": None,
                    "mid": None,
                    "spread": None
                }
            
            return {
                "bid": self.current_best_bid,
                "ask": self.current_best_ask,
                "mid": self.current_mid_price,
                "spread": self.current_best_ask - self.current_best_bid
            }

    def get_market(self) -> Optional[Dict]:
        """
        Check if this market exists and get market details.
        
        Returns:
            Market details if it exists, None otherwise
        """
        try:
            market = self.clob_client.get_market(market=self.fpmm_address)
            if market:
                logging.info(f"✅ Market exists: {self.fpmm_address}")
                return market
            else:
                logging.warning(f"❌ Market not found: {self.fpmm_address}")
                return None
        except Exception as e:
            logging.error(f"Error checking market: {e}")
            return None

    def _place_order_async(self, order: Order) -> Optional[str]:
        """
        Internal method to place an order via CLOB API (runs in executor).
        
        Args:
            order: Order object to place
            
        Returns:
            Order ID if successful, None otherwise
        """
        try:
            # Determine side (BUY or SELL constant from py-clob-client)
            side = BUY if order.is_buy else SELL
            
            # Create OrderArgs object per latest Polymarket API
            order_args = OrderArgs(
                price=order.price,
                size=order.size,
                side=side,
                token_id=self.asset_id
            )
            
            # Create and sign the order
            signed_order = self.clob_client.create_order(order_args)
            
            # Post order to CLOB
            response = self.clob_client.post_order(signed_order)
            
            if response and response.get("orderId"):
                return response["orderId"]
            else:
                logging.error(f"Invalid response posting order: {response}")
                return None
                
        except Exception as e:
            logging.error(f"Error placing order: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _cancel_order_async(self, order_id: str) -> bool:
        """
        Internal method to cancel an order via CLOB API (runs in executor).
        
        Args:
            order_id: ID of order to cancel
            
        Returns:
            True if successful, False otherwise
        """
        try:
            response = self.clob_client.cancel_order(order_id=order_id)
            return response and response.get("status") == "success"
        except Exception as e:
            logging.error(f"Error cancelling order {order_id}: {e}")
            return False

    def disconnect(self):
        """Cleanup and disconnect from WebSocket and CLOB"""
        if self.ws_client:
            self.ws_client.disconnect()
        self._executor.shutdown(wait=False)
        logging.info("Disconnected from Polymarket")

