"""
Setup Token Allowances for Polymarket Proxy Wallets
====================================================
This script helps you set up allowances for proxy wallets (MetaMask, browser wallets).

IMPORTANT: For proxy wallets, you CANNOT directly set allowances via Python.
You must use the Polymarket web interface.

This script provides instructions and checks your setup.

Usage:
1. Run: python setup_proxy_allowances.py
2. Follow the instructions to set allowances via Polymarket.com
3. Verify with: python check_allowances.py
"""

import os
from dotenv import load_dotenv

load_dotenv()

funder_address = os.getenv("FUNDER_ADDRESS", "Not set")
priv_key = os.getenv("PRIV_KEY", "")

# Derive owner address
owner_address = "Not available"
if priv_key:
    try:
        from web3 import Web3
        w3 = Web3()
        owner_address = w3.eth.account.from_key(priv_key).address
    except:
        pass

is_proxy = (owner_address != "Not available" and owner_address.lower() != funder_address.lower())

print("=" * 80)
print("POLYMARKET PROXY WALLET ALLOWANCE SETUP")
print("=" * 80)
print()
print(f"Owner address:  {owner_address}")
print(f"Funder address: {funder_address}")
print(f"Wallet type:    {'PROXY (MetaMask/Browser)' if is_proxy else 'EOA (Direct)' if owner_address != 'Not available' else 'Unknown'}")
print()

if not is_proxy:
    print("⚠️  You don't appear to have a proxy wallet setup.")
    print("   If you have an EOA wallet, run: python setup_allowances.py")
    print()
    exit(0)

print("⚠️  IMPORTANT: For proxy wallets, allowances must be set via Polymarket website.")
print()
print("Since the proxy is a smart contract you don't directly control with a")
print("private key, you MUST use the Polymarket web interface.")
print()
print("=" * 80)
print("STEP-BY-STEP GUIDE (Takes 30 seconds)")
print("=" * 80)
print()
print("1️⃣  Open your browser and go to: https://polymarket.com")
print()
print("2️⃣  Connect MetaMask:")
print("   - Click 'Connect Wallet' in top right")
print("   - Select MetaMask")
print(f"   - Verify it shows: {funder_address[:10]}...{funder_address[-8:]}")
print()
print("3️⃣  Navigate to any market where you have tokens:")
print("   - Go to 'Portfolio' or search for a market")
print("   - Examples: 'ETH', 'BTC', 'SOL' markets")
print()
print("4️⃣  Try to SELL some tokens:")
print("   - Click on a token you own")
print("   - Click 'Sell' button")
print("   - Enter any amount (even 1 share)")
print("   - Click 'Place Order'")
print()
print("5️⃣  Approve the transaction in MetaMask:")
print("   - MetaMask will popup asking for TWO approvals:")
print("     a) CTF Contract Approval (this is what we need!) ✅")
print("     b) Order Signature (to place the order)")
print("   - Approve BOTH")
print()
print("6️⃣  Done! The approval is permanent - you only do this once.")
print()
print("=" * 80)
print("VERIFICATION")
print("=" * 80)
print()
print("After completing the steps above, verify your setup:")
print()
print("   python check_allowances.py")
print()
print("This will show you which allowances are set correctly.")
print()
print("=" * 80)
print("TROUBLESHOOTING")
print("=" * 80)
print()
print("❓ MetaMask shows different address?")
print("   → You may have multiple wallets. Make sure you select the one")
print(f"     that matches: {funder_address}")
print()
print("❓ Don't have any tokens to sell?")
print("   → Buy a small amount first (even $1) to trigger the approval")
print()
print("❓ Approval transaction fails?")
print("   → Ensure you have POL/MATIC for gas fees (needs ~$0.01)")
print()
print("❓ Still getting errors?")
print("   → Run: python check_allowances.py")
print("   → Contact Polymarket support")
print()
print("=" * 80)
print()
print("🎯 Quick Summary:")
print()
print("   1. Go to polymarket.com")
print("   2. Connect MetaMask")
print("   3. Sell any token")
print("   4. Approve CTF contract")
print("   5. Run: python check_allowances.py")
print("   6. Start trading: python profitable_balanced_scalper.py")
print()
print("=" * 80)
print()
