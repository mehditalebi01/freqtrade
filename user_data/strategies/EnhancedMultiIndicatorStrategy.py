"""
Enhanced Multi-Indicator Momentum Strategy
===========================================

This strategy is an evolution of ``MultiIndicatorStrategy`` with lighter conditions
and better risk control.  It trades on a 5‑minute timeframe with confirmation from
the 1‑hour timeframe.  Entry rules require a golden cross of EMA9/EMA21 and basic
trend filters, but the pullback condition has been relaxed to allow trades that
aren't too far above the Bollinger midpoint.  The strategy also confirms that the
macro trend is bullish by requiring the 1‑hour EMA50 above the 1‑hour EMA200.  It
includes hyperoptable parameters for RSI, ADX and volume multiplier.

Key differences from ``MultiIndicatorStrategy``:
    - Reduced stoploss to –5 % and smaller trailing offset (1.5 % offset, 0.5 % trail)
    - Minimal ROI schedule lowered to more realistic values for the 5 m timeframe.
    - Entry condition now allows price up to the middle Bollinger band plus 10 % of
      the band width, instead of strictly below the middle band.
    - Macro trend filter: 1h EMA50 must be above 1h EMA200.
    - Custom stoploss tightens earlier (after 30 min, 60 min).

This strategy is long‑only by default, but can be extended to short trades by
setting ``can_short = True`` and adding appropriate short conditions.
"""

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import (
    IStrategy,
    Trade,
    informative,
)
from technical import qtpylib


class EnhancedMultiIndicatorStrategy(IStrategy):
    """Implementation of the enhanced multi-indicator strategy."""

    INTERFACE_VERSION = 3
    can_short = False

    # --- Configuration parameters ---
    timeframe = "5m"
    process_only_new_candles = True

    # ROI table: smaller targets for shorter time horizons
    minimal_roi = {
        "60": 0.02,  # 2% after 1h
        "30": 0.03,  # 3% after 30m
        "0": 0.04,   # 4% immediately
    }

    # Base stoploss and trailing configuration
    stoploss = -0.05
    trailing_stop = True
    trailing_stop_positive = 0.005
    trailing_stop_positive_offset = 0.015
    trailing_only_offset_is_reached = True

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    use_custom_stoploss = True

    startup_candle_count: int = 200

    # Hyperopt parameters
    from freqtrade.strategy import IntParameter, DecimalParameter
    buy_rsi_low = IntParameter(low=20, high=40, default=30, space="buy", optimize=True, load=True)
    buy_rsi_high = IntParameter(low=55, high=75, default=65, space="buy", optimize=True, load=True)
    sell_rsi = IntParameter(low=60, high=80, default=70, space="sell", optimize=True, load=True)
    buy_adx = IntParameter(low=15, high=25, default=20, space="buy", optimize=True, load=True)
    buy_volume_mult = DecimalParameter(
        low=1.0, high=1.5, default=1.2, decimals=1, space="buy", optimize=True, load=True
    )

    # Plot colours for backtest plots
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
            "RSI": {"rsi": {"color": "#7fba3c"}},
            "MACD": {
                "macd": {"color": "#1c78d4"},
                "macdsignal": {"color": "#ff6600"},
                "macdhist": {"color": "#cccccc", "type": "bar"},
            },
            "ADX": {"adx": {"color": "#d4a11c"}},
        },
    }

    def informative_pairs(self):
        """Return additional pairs for higher timeframe (1h) confirmation."""
        pairs = self.dp.current_whitelist()
        return [(pair, "1h") for pair in pairs]

    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Populate EMA50, EMA200 and RSI on the 1h timeframe."""
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Compute indicators on the 5m timeframe."""
        dataframe["ema9"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema21"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)

        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]

        bb = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2)
        dataframe["bb_lowerband"] = bb["lower"]
        dataframe["bb_middleband"] = bb["mid"]
        dataframe["bb_upperband"] = bb["upper"]

        band_width = dataframe["bb_upperband"] - dataframe["bb_lowerband"]
        dataframe["bb_mid_upper"] = dataframe["bb_middleband"] + 0.10 * band_width

        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)

        dataframe["volume_sma"] = ta.SMA(dataframe["volume"], timeperiod=20)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Generate entry signals for long positions."""
        conditions = (
            qtpylib.crossed_above(dataframe["ema9"], dataframe["ema21"])
            & (dataframe["close"] > dataframe["ema50"])
            & (dataframe["rsi"] > self.buy_rsi_low.value)
            & (dataframe["rsi"] < self.buy_rsi_high.value)
            & (dataframe["macdhist"] > 0)
            & (dataframe["close"] <= dataframe["bb_mid_upper"])
            & (dataframe["adx"] > self.buy_adx.value)
            & (dataframe["volume"] > (dataframe["volume_sma"] * self.buy_volume_mult.value))
            & (dataframe["ema50_1h"] > dataframe["ema200_1h"])
            & (dataframe["rsi_1h"] < 65)
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[conditions, ["enter_long", "enter_tag"]] = (1, "enhanced_entry")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Generate exit signals for long positions."""
        dataframe.loc[
            qtpylib.crossed_below(dataframe["ema9"], dataframe["ema21"]) & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "death_cross")

        dataframe.loc[
            (dataframe["rsi"] > self.sell_rsi.value) & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "rsi_overbought")

        dataframe.loc[
            (dataframe["close"] > dataframe["bb_upperband"]) & (dataframe["rsi"] > 65) & (dataframe["volume"] > 0),
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
    ) -> Optional[float]:
        """Adaptive stoploss: tighten as trade ages."""
        elapsed = current_time - trade.open_date_utc
        if elapsed > timedelta(minutes=60):
            return -0.025
        if elapsed > timedelta(minutes=30):
            return -0.04
        return None