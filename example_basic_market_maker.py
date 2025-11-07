"""
Basic Market Making Bot Example
Demonstrates how to use the PolymarketInterface to run a simple market maker on a single market.

This example:
1. Initializes the CLOB interface
2. Connects to real-time WebSocket price feeds
3. Places bid/ask orders around the mid-price
4. Monitors open orders
5. Rebalances orders as market moves

Usage:
    Set PRIV_KEY and FUNDER_ADDRESS environment variables, then:
    python example_basic_market_maker.py
"""

import logging
import time
import os
from polymarketInterface import PolymarketInterface, Order

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Run a simple market maker on a single market"""
    
    # Configuration - UPDATE THESE FOR YOUR MARKET
    FPMM_ADDRESS = "0x0000..."  # Replace with actual FPMM address from Polymarket
    ASSET_ID = "123456789"      # Replace with actual asset ID
    SPREAD = 0.02               # 2% spread (bid and ask)
    ORDER_SIZE = 100            # Size of each order (in outcome tokens)
    
    logger.info(f"Starting Market Maker Bot")
    logger.info(f"  FPMM: {FPMM_ADDRESS}")
    logger.info(f"  Asset ID: {ASSET_ID}")
    logger.info(f"  Spread: {SPREAD*100:.1f}%")
    logger.info(f"  Order Size: {ORDER_SIZE}")
    
    try:
        # Initialize interface
        logger.info("Initializing Polymarket interface...")
        interface = PolymarketInterface(
            fpmm_address=FPMM_ADDRESS,
            asset_id=ASSET_ID,
            refresh_frequency=1
        )
        
        # Verify market exists
        logger.info("Validating market exists...")
        market = interface.get_market()
        if not market:
            logger.error("Market not found!")
            return False
        logger.info(f"✅ Market validated")
        
        # Connect to real-time prices
        logger.info("Connecting to real-time price feed...")
        if not interface.connect_ws():
            logger.error("Failed to connect to WebSocket")
            return False
        
        # Wait for first price update
        logger.info("Waiting for initial price data...")
        for i in range(30):  # Wait up to 30 seconds
            prices = interface.get_price()
            if prices['mid'] is not None:
                logger.info(f"✅ Received initial prices: Bid=${prices['bid']:.4f}, Ask=${prices['ask']:.4f}")
                break
            time.sleep(1)
        else:
            logger.warning("No price data received within timeout")
        
        # Main market making loop
        logger.info("Entering market making loop...")
        iteration = 0
        open_order_ids = []
        
        while True:
            iteration += 1
            try:
                # Get current prices
                prices = interface.get_price()
                
                if prices['mid'] is None:
                    logger.warning(f"[Iteration {iteration}] No price data available, retrying...")
                    time.sleep(interface.refresh_frequency)
                    continue
                
                mid_price = prices['mid']
                logger.info(f"[Iteration {iteration}] Mid Price: ${mid_price:.4f}, Spread: ${prices['spread']:.4f}")
                
                # Calculate bid and ask prices
                bid_price = mid_price * (1 - SPREAD / 2)
                ask_price = mid_price * (1 + SPREAD / 2)
                
                # Get current open orders
                open_orders = interface.get_orders()
                logger.info(f"  Current open orders: {len(open_orders)}")
                
                # If no open orders, place new ones
                if len(open_orders) == 0:
                    logger.info(f"  Placing new orders: BUY @ ${bid_price:.4f} | SELL @ ${ask_price:.4f}")
                    
                    new_orders = [
                        Order(size=ORDER_SIZE, price=bid_price, is_buy=True),
                        Order(size=ORDER_SIZE, price=ask_price, is_buy=False)
                    ]
                    
                    if interface.place_orders(new_orders):
                        logger.info(f"  ✅ Orders placed successfully")
                        open_order_ids = [order.id for order in new_orders if order.id]
                    else:
                        logger.warning(f"  ❌ Failed to place orders")
                
                # Check if prices moved significantly (> 5% from mid)
                # This is a simple rebalancing trigger
                elif prices['spread'] is not None and prices['spread'] > mid_price * 0.05:
                    logger.info(f"  Large spread detected ({prices['spread']:.4f}), rebalancing...")
                    
                    # Cancel existing orders
                    if open_order_ids:
                        logger.info(f"  Cancelling {len(open_order_ids)} orders...")
                        interface.cancel_orders(open_order_ids)
                        open_order_ids = []
                    
                    # Place new orders at adjusted prices
                    new_orders = [
                        Order(size=ORDER_SIZE, price=bid_price, is_buy=True),
                        Order(size=ORDER_SIZE, price=ask_price, is_buy=False)
                    ]
                    
                    if interface.place_orders(new_orders):
                        logger.info(f"  ✅ New orders placed after rebalancing")
                        open_order_ids = [order.id for order in new_orders if order.id]
                    else:
                        logger.warning(f"  ❌ Failed to place rebalancing orders")
                
                # Log stats
                ws_stats = interface.ws_client.get_stats() if interface.ws_client else {}
                logger.info(f"  WS Messages: {ws_stats.get('message_count', 'N/A')} | "
                          f"Markets: {ws_stats.get('market_snapshots', 'N/A')}")
                
                # Wait before next iteration
                time.sleep(interface.refresh_frequency)
                
            except KeyboardInterrupt:
                logger.info("Received interrupt signal")
                break
            except Exception as e:
                logger.error(f"Error in iteration {iteration}: {e}", exc_info=True)
                time.sleep(5)  # Wait before retrying
        
        # Cleanup: cancel any remaining orders
        logger.info("Cleaning up...")
        if open_order_ids:
            logger.info(f"Cancelling {len(open_order_ids)} remaining orders...")
            interface.cancel_orders(open_order_ids)
        
        interface.disconnect()
        logger.info("✅ Market maker stopped cleanly")
        return True
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    # Verify environment variables
    if not os.getenv("PRIV_KEY") or not os.getenv("FUNDER_ADDRESS"):
        print("\n❌ ERROR: Missing environment variables!")
        print("\nSet these before running:")
        print("  export PRIV_KEY='your_private_key'")
        print("  export FUNDER_ADDRESS='your_funder_address'")
        print("\nOr create a .env file with these variables")
        exit(1)
    
    success = main()
    exit(0 if success else 1)

