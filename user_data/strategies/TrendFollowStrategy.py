"""
TrendFollowStrategy
===================

This file contains a moderately sophisticated example strategy for Freqtrade. The
goal of this strategy is to provide a solid baseline for spot trading on
liquid pairs (for example, large-cap or mid-cap USDT pairs on Binance) while
remaining fully compatible with Freqtrade's hyper-optimization (`hyperopt`). The
strategy only trades long (no shorts) and uses multiple timeframes to filter
market conditions. It combines classic trend following and momentum
indicators (exponential moving averages and the Relative Strength Index) with a
basic volume filter and trailing stop logic. Key numerical parameters are
exposed via `freqtrade.strategy` Parameter objects so they can be tuned
automatically. This file should be placed in your `user_data/strategies`
directory and loaded via `freqtrade trade --strategy TrendFollowStrategy`.

Strategy overview
-----------------

* **Timeframes**: Uses a 5-minute base timeframe (`timeframe = "5m"`) and a
  1-hour informative timeframe. Trades are only taken when the higher
  timeframe is bullish.
* **Trend filter**: Price must be above the 200-period EMA on both 5-minute and
  1-hour charts to consider long entries.
* **Momentum trigger**: A fast EMA crossing above a slow EMA on the 5-minute
  chart together with an RSI rising above a configurable threshold signals
  momentum in the direction of the trend.
* **Volume filter**: The current volume must exceed its rolling 20-period
  average. This prevents entries during illiquid candles.
* **Exit logic**: Positions exit when the fast EMA crosses back below the slow
  EMA or when the RSI falls below a configurable exit threshold. A minimal
  ROI table and a trailing stop ensure profits are realised and losses cut.
* **Risk management**: A fixed stoploss of -10% protects against large
  drawdowns, while a trailing stop with offset locks in profits. The
  `minimal_roi` dictionary defines profit targets at 0, 60, 180 and 360 minutes.

Refer to the official Freqtrade documentation for details on strategy design,
backtesting and hyper-optimization. In particular, avoid lookahead bias by
never referencing future candles (see the **Common mistakes** section in the
strategy customisation docs). Always backtest on realistic data, enable
fees/slippage and forward test in dry-run mode before going live.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from pandas import DataFrame

import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib

from freqtrade.strategy import (
    IStrategy,
    informative,
    merge_informative_pair,
    timeframe_to_minutes,
)
from freqtrade.strategy import IntParameter, DecimalParameter
from functools import reduce

class TrendFollowStrategy(IStrategy):
    """Trend following strategy with hyperoptable parameters and multi-timeframe support."""

    # Freqtrade interface version
    INTERFACE_VERSION: int = 3

    # We trade on a 5 minute timeframe
    timeframe: str = "5m"

    # Use one informative timeframe: 1 hour
    informative_timeframe: str = "1h"

    # Number of candles the strategy needs before producing signals. Set high
    # enough to compute long moving averages on both timeframes. 200 EMA on
    # 1h timeframe requires at least 200 * 12 (because 1h contains 12x5m
    # candles) = 2400 candles on the 5m timeframe. We choose 2500 to be
    # conservative.
    startup_candle_count: int = 2500

    # Expose hyperoptable parameters for moving average lengths and RSI
    # thresholds. Hyperopt will search within these ranges for optimal values.
    buy_ema_fast = IntParameter(5, 20, default=12, space="buy", optimize=True)
    buy_ema_slow = IntParameter(20, 60, default=26, space="buy", optimize=True)
    buy_rsi_threshold = IntParameter(45, 60, default=50, space="buy", optimize=True)

    sell_rsi_threshold = IntParameter(30, 50, default=40, space="sell", optimize=True)

    # Risk management – fixed stoploss and trailing stop parameters. These
    # values can also be optimized via hyperopt by defining a custom
    # hyperopt class. See the Freqtrade docs on hyperopt for details.
    stoploss: float = -0.10
    minimal_roi: Dict[str, float] = {
        # Immediately take profit at 5%
        "0": 0.05,
        # After one hour (60 minutes) accept 3%
        "60": 0.03,
        # After three hours accept 1%
        "180": 0.01,
        # After six hours break even
        "360": 0,
    }
    trailing_stop: bool = True
    trailing_stop_positive: float = 0.02
    trailing_stop_positive_offset: float = 0.04
    trailing_only_offset_is_reached: bool = True

    # Freqtrade will call this method to determine which informative pairs
    # are required. We request the same pair on the 1h timeframe for all
    # whitelisted pairs.
    def informative_pairs(self) -> List[Tuple[str, str]]:
        return [(pair, self.informative_timeframe) for pair in self.dp.current_whitelist()]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Compute indicators for both base and informative dataframes."""
        # Compute moving averages and RSI on the base timeframe (5m)
        ema_fast_len = int(self.buy_ema_fast.value)
        ema_slow_len = int(self.buy_ema_slow.value)
        dataframe["ema_fast"] = ta.EMA(dataframe["close"], timeperiod=ema_fast_len)
        dataframe["ema_slow"] = ta.EMA(dataframe["close"], timeperiod=ema_slow_len)
        dataframe["ema200"] = ta.EMA(dataframe["close"], timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe["close"], timeperiod=14)

        # Volume filter – rolling mean of volume over last 20 candles
        dataframe["vol_mean"] = dataframe["volume"].rolling(window=20).mean()

        # Informative timeframe indicators. The dp (DataProvider) already
        # fetched the 1h data because we declared it in `informative_pairs()`.
        pair = metadata["pair"]
        informative = self.dp.get_pair_dataframe(pair=pair, timeframe=self.informative_timeframe)
        # Compute indicators on informative timeframe
        informative["ema_fast_inf"] = ta.EMA(informative["close"], timeperiod=ema_fast_len)
        informative["ema_slow_inf"] = ta.EMA(informative["close"], timeperiod=ema_slow_len)
        informative["ema200_inf"] = ta.EMA(informative["close"], timeperiod=200)
        informative["rsi_inf"] = ta.RSI(informative["close"], timeperiod=14)

        # Merge informative dataframe into main dataframe. Freqtrade will
        # automatically align timestamps and forward fill missing values so
        # informative indicators are accessible with the suffix `_1h`.
        dataframe = merge_informative_pair(
            dataframe,
            informative,
            self.timeframe,
            self.informative_timeframe,
            ffill=True
        )

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Generate entry signals for long positions."""
        conditions: List[pd.Series] = []

        # Trend filter: price above EMA200 on both timeframes
        conditions.append(dataframe["close"] > dataframe["ema200"])
        conditions.append(dataframe["close_1h"] > dataframe["ema200_inf_1h"])

        # Momentum trigger: fast EMA crosses above slow EMA on the base timeframe
        conditions.append(qtpylib.crossed_above(dataframe["ema_fast"], dataframe["ema_slow"]))

        # RSI confirmation: RSI above threshold on base timeframe and above 50 on the informative timeframe
        conditions.append(dataframe["rsi"] > self.buy_rsi_threshold.value)
        conditions.append(dataframe["rsi_inf_1h"] > 50)

        # Volume must be greater than its rolling mean (to avoid thin candles)
        conditions.append(dataframe["volume"] > 0)
        conditions.append(dataframe["volume"] > dataframe["vol_mean"])

        if conditions:
            dataframe.loc[
                reduce(lambda x, y: x & y, conditions),
                "enter_long",
            ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Generate exit signals for long positions."""
        conditions: List[pd.Series] = []

        # Exit when fast EMA crosses below slow EMA
        conditions.append(qtpylib.crossed_below(dataframe["ema_fast"], dataframe["ema_slow"]))

        # Or when RSI drops below the sell threshold
        conditions.append(dataframe["rsi"] < self.sell_rsi_threshold.value)

        if conditions:
            dataframe.loc[
                reduce(lambda x, y: x | y, conditions),
                "exit_long",
            ] = 1

        return dataframe

