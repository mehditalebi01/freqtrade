# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
# --- Do not remove these imports ---
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from pandas import DataFrame
from typing import Optional, Union

from freqtrade.strategy import (
    IStrategy,
    Trade,
    Order,
    PairLocks,
    informative,  # @informative decorator
    # Hyperopt Parameters
    BooleanParameter,
    CategoricalParameter,
    DecimalParameter,
    IntParameter,
    RealParameter,
    # timeframe helpers
    timeframe_to_minutes,
    timeframe_to_next_date,
    timeframe_to_prev_date,
    # Strategy helper functions
    merge_informative_pair,
    stoploss_from_absolute,
    stoploss_from_open,
)

# --------------------------------
# Add your lib to import here
import talib.abstract as ta
from technical import qtpylib


class MultiIndicatorStrategy(IStrategy):
    """
    Multi-Indicator Momentum Strategy
    ==================================
    A professional trading strategy combining 6 proven technical indicators
    with multi-timeframe confirmation and dynamic risk management.

    Indicators:
        - EMA 9/21/50/200: Trend direction & crossover signals
        - RSI (14): Overbought/oversold detection
        - MACD (12/26/9): Momentum confirmation
        - Bollinger Bands (20, 2σ): Volatility & mean reversion
        - ADX (14): Trend strength filter
        - Volume SMA (20): Volume confirmation

    Entry (Long):
        1. MARKET REGIME: 1h EMA50 > 1h EMA200 (bull market only)
        2. EMA 9 crosses above EMA 21 (golden cross)
        3. Price above EMA 50 (medium-term uptrend)
        4. RSI between 25-70 (not overbought, room to run)
        5. MACD histogram positive (bullish momentum)
        6. ADX > 20 (trend has strength)
        7. Volume above average (confirmation)
        8. 1h RSI < 70 (not overbought on higher TF)

    Exit (Long):
        1. EMA 9 crosses below EMA 21 (death cross)
        2. RSI > 70 (overbought)
        3. Price above BB upper + RSI > 65 (overextended)

    Risk Management:
        - Hard stoploss: -5%
        - Profit-aware custom stoploss
        - Trailing stop after 1.5% profit
    """

    # Strategy interface version
    INTERFACE_VERSION = 3

    # Can this strategy go short?
    can_short: bool = False

    # Minimal ROI designed for the strategy
    minimal_roi = {
        "90": 0.005,   # 0.5% after 90 minutes
        "60": 0.01,    # 1% after 1 hour
        "30": 0.02,    # 2% after 30 minutes
        "0": 0.04,     # 4% immediately
    }

    # Optimal stoploss (tighter to limit losses)
    stoploss = -0.05

    # Trailing stoploss (activated after offset is reached)
    trailing_stop = True
    trailing_stop_positive = 0.008
    trailing_stop_positive_offset = 0.015
    trailing_only_offset_is_reached = True

    # 15m timeframe (less noise than 5m)
    timeframe = "15m"

    # Run "populate_indicators()" only for new candle
    process_only_new_candles = True

    # These values can be overridden in the config
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Enable custom stoploss
    use_custom_stoploss = True

    # Number of candles the strategy requires before producing valid signals
    startup_candle_count: int = 200

    # ---- Hyperoptable Parameters ----

    # Entry RSI thresholds
    buy_rsi_low = IntParameter(low=15, high=35, default=25, space="buy", optimize=True, load=True)
    buy_rsi_high = IntParameter(low=60, high=80, default=70, space="buy", optimize=True, load=True)

    # Exit RSI threshold
    sell_rsi = IntParameter(low=60, high=80, default=70, space="sell", optimize=True, load=True)

    # ADX threshold
    buy_adx = IntParameter(low=15, high=30, default=20, space="buy", optimize=True, load=True)

    # Volume multiplier
    buy_volume_mult = DecimalParameter(
        low=0.8, high=2.0, default=1.0, decimals=1, space="buy", optimize=True, load=True
    )

    # Optional order type mapping
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    # Optional order time in force
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    plot_config = {
        "main_plot": {
            "ema9": {"color": "#00ff00"},
            "ema21": {"color": "#ff6600"},
            "ema50": {"color": "#0066ff"},
            "ema200": {"color": "#ff0000"},
            "bb_upperband": {"color": "#aaaaaa"},
            "bb_lowerband": {"color": "#aaaaaa"},
        },
        "subplots": {
            "RSI": {
                "rsi": {"color": "#7fba3c"},
            },
            "MACD": {
                "macd": {"color": "#1c78d4"},
                "macdsignal": {"color": "#ff6600"},
                "macdhist": {"color": "#cccccc", "type": "bar"},
            },
            "ADX": {
                "adx": {"color": "#d4a11c"},
            },
        },
    }

    def informative_pairs(self):
        """
        Define additional, informative pair/interval combinations to be cached.
        We use the 1h timeframe for higher-timeframe trend confirmation.
        """
        pairs = self.dp.current_whitelist()
        informative_pairs = [(pair, "1h") for pair in pairs]
        return informative_pairs

    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        1-hour timeframe indicators for trend confirmation and market regime.
        Only enter trades when the higher timeframe shows bullish conditions.
        """
        # EMA 50 & 200 on 1h for market regime detection
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)

        # RSI on 1h to avoid buying into overbought conditions
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Adds multiple technical analysis indicators to the given DataFrame.

        Performance Note: Only indicators actually used in entry/exit logic are computed.
        """

        # ---- Exponential Moving Averages ----
        dataframe["ema9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)

        # ---- RSI (Relative Strength Index) ----
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # ---- MACD (Moving Average Convergence Divergence) ----
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]

        # ---- Bollinger Bands ----
        bollinger = qtpylib.bollinger_bands(
            qtpylib.typical_price(dataframe), window=20, stds=2
        )
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]
        dataframe["bb_percent"] = (dataframe["close"] - dataframe["bb_lowerband"]) / (
            dataframe["bb_upperband"] - dataframe["bb_lowerband"]
        )

        # ---- ADX (Average Directional Index) ----
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)

        # ---- Volume SMA ----
        dataframe["volume_sma"] = ta.SMA(dataframe["volume"], timeperiod=20)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Entry signals based on multi-indicator confluence.

        Long entry requires ALL conditions:
        1. EMA 9 crosses above EMA 21 (golden cross)
        2. Price is above EMA 50 (medium-term uptrend)
        3. RSI is in the buy zone (not overbought)
        4. MACD histogram is positive (bullish momentum)
        5. ADX > threshold (trend has strength, avoid chop)
        6. Volume is above average (confirmation)
        7. Higher TF confirmation: price above 1h EMA 50 + RSI < 70
        """
        conditions_long = (
            # MARKET REGIME: Only trade in bull market (1h EMA50 > 1h EMA200)
            (dataframe["ema50_1h"] > dataframe["ema200_1h"])
            # Signal: EMA 9 crosses above EMA 21 (golden cross)
            & (qtpylib.crossed_above(dataframe["ema9"], dataframe["ema21"]))
            # Guard: Price above EMA 50 (medium-term uptrend)
            & (dataframe["close"] > dataframe["ema50"])
            # Guard: RSI in buy zone (not overbought, has room to run)
            & (dataframe["rsi"] > self.buy_rsi_low.value)
            & (dataframe["rsi"] < self.buy_rsi_high.value)
            # Guard: MACD histogram positive (bullish momentum)
            & (dataframe["macdhist"] > 0)
            # Guard: ADX above threshold (trend has strength)
            & (dataframe["adx"] > self.buy_adx.value)
            # Guard: Volume above average
            & (dataframe["volume"] > (dataframe["volume_sma"] * self.buy_volume_mult.value))
            # Guard: Price above 1h EMA 50
            & (dataframe["close"] > dataframe["ema50_1h"])
            # Guard: 1h RSI not overbought
            & (dataframe["rsi_1h"] < 70)
            # Guard: Volume is not 0
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[conditions_long, ["enter_long", "enter_tag"]] = (1, "ema_cross_momentum")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Exit signals based on trend weakness indicators.

        Long exit triggers on ANY condition:
        1. EMA 9 crosses below EMA 21 (death cross)
        2. RSI enters overbought territory (> 70)
        3. Price overextended above BB upper + overbought RSI
        """

        # Exit 1: Death cross (trend reversal)
        dataframe.loc[
            (
                (qtpylib.crossed_below(dataframe["ema9"], dataframe["ema21"]))
                & (dataframe["volume"] > 0)
            ),
            ["exit_long", "exit_tag"],
        ] = (1, "ema_death_cross")

        # Exit 2: RSI overbought
        dataframe.loc[
            (
                (dataframe["rsi"] > self.sell_rsi.value)
                & (dataframe["volume"] > 0)
            ),
            ["exit_long", "exit_tag"],
        ] = (1, "rsi_overbought")

        # Exit 3: Price overextended (above BB upper + RSI > 65)
        dataframe.loc[
            (
                (dataframe["close"] > dataframe["bb_upperband"])
                & (dataframe["rsi"] > 65)
                & (dataframe["volume"] > 0)
            ),
            ["exit_long", "exit_tag"],
        ] = (1, "bb_overextended")

        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        """
        Profit-aware custom stoploss.

        - In profit > 2%: tight 1.5% trailing (lock in gains)
        - In profit 0-2%: 3% trailing (protect small gains)
        - In loss after 4 hours: cut at -4% (don't hold losers)
        - First 4 hours in loss: let initial -8% stoploss handle
        """
        # If in good profit, protect it
        if current_profit > 0.02:
            return -0.015

        # If in small profit, moderate trailing
        if current_profit > 0:
            return -0.03

        # After 4 hours in loss, tighten to -4%
        if current_time - timedelta(minutes=240) > trade.open_date_utc:
            return -0.04

        # First 4 hours: let initial stoploss handle it
        return None
