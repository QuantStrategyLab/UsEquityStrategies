"""Additive baseline manifests; the historical manifest registry stays frozen."""
from us_equity_strategies.buy_and_hold import MANIFESTS

nasdaq100_buy_and_hold_manifest = MANIFESTS["nasdaq100_buy_and_hold"]
sp500_buy_and_hold_manifest = MANIFESTS["sp500_buy_and_hold"]

__all__ = ["MANIFESTS", "nasdaq100_buy_and_hold_manifest", "sp500_buy_and_hold_manifest"]
