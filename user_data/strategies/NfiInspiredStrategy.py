# --- NFI-Inspired Strategy for Freqtrade ---
# Inspired by NostalgiaForInfinity (https://github.com/iterativv/NostalgiaForInfinity)
# Multi-indicator approach targeting high winrate and low drawdown on futures.
# Timeframe: 5m with 1h informative for trend confirmation.

import logging
from datetime import datetime
from functools import reduce

import numpy as np
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    BooleanParameter,
    DecimalParameter,
    IntParameter,
    IStrategy,
    informative,
    merge_informative_pair,
)

logger = logging.getLogger(__name__)


class NfiInspiredStrategy(IStrategy):
    """
    NFI-Inspired Strategy
    - 5m timeframe with 1h informative
    - Multi-indicator entries with strict confirmation (high winrate)
    - Conservative risk management (low drawdown)
    - Futures long-only
    """

    INTERFACE_VERSION = 3

    # --- Timeframe ---
    timeframe = "5m"
    startup_candle_count = 200

    # --- Can short ---
    can_short = False

    # --- ROI ---
    minimal_roi = {
        "0": 0.177,
        "40": 0.053,
        "68": 0.02,
        "150": 0
    }

    # --- Stoploss ---
    stoploss = -0.242

    # --- Trailing Stop ---
    trailing_stop = True
    trailing_stop_positive = 0.35
    trailing_stop_positive_offset = 0.375
    trailing_only_offset_is_reached = True

    # --- Order Types ---
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "emergency_exit": "market",
        "force_entry": "market",
        "force_exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
        "stoploss_on_exchange_interval": 60,
        "stoploss_on_exchange_limit_ratio": 0.99,
    }

    # --- Unfilledtimeout ---
    unfilledtimeout = {
        "entry": 3,
        "exit": 2,
        "exit_timeout_count": 0,
        "unit": "minutes",
    }

    # --- Exit Signal Config ---
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True

    # --- Process only new candles ---
    process_only_new_candles = True

    # ============================================================
    # Hyperopt Parameters
    # ============================================================

    # --- Entry: RSI ---
    buy_rsi_fast = IntParameter(15, 40, default=30, space="buy", optimize=True)
    buy_rsi_slow = IntParameter(35, 60, default=52, space="buy", optimize=True)

    # --- Entry: EMA ---
    buy_ema_fast = IntParameter(5, 30, default=12, space="buy", optimize=True)
    buy_ema_slow = IntParameter(20, 60, default=53, space="buy", optimize=True)

    # --- Entry: Bollinger Band ---
    buy_bb_width_min = DecimalParameter(0.01, 0.10, default=0.046, space="buy", optimize=True)
    buy_bb_delta = DecimalParameter(0.001, 0.03, default=0.021, space="buy", optimize=True)

    # --- Entry: Volume ---
    buy_volume_factor = DecimalParameter(1.0, 3.0, default=1.975, space="buy", optimize=True)

    # --- Entry: 1h Trend ---
    buy_ema_1h_diff = DecimalParameter(0.0, 0.05, default=0.02, space="buy", optimize=True)
    buy_rsi_1h_min = IntParameter(30, 55, default=52, space="buy", optimize=True)
    buy_rsi_1h_max = IntParameter(65, 85, default=76, space="buy", optimize=True)

    # --- Exit: RSI ---
    sell_rsi = IntParameter(60, 85, default=80, space="sell", optimize=True)

    # --- Exit: MACD ---
    sell_macd_cross = BooleanParameter(default=False, space="sell", optimize=True)

    # ============================================================
    # Informative 1h Indicators
    # ============================================================

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(pair, "1h") for pair in pairs]

    def populate_informative_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Calculate informative 1h indicators."""
        # EMAs
        dataframe["ema_12"] = ta.EMA(dataframe, timeperiod=12)
        dataframe["ema_26"] = ta.EMA(dataframe, timeperiod=26)
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_100"] = ta.EMA(dataframe, timeperiod=100)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)

        # RSI
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # MACD
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]

        # Bollinger Bands
        bb = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_upper_1h"] = bb["upperband"]
        dataframe["bb_lower_1h"] = bb["lowerband"]
        dataframe["bb_mid_1h"] = bb["middleband"]

        return dataframe

    # ============================================================
    # Main 5m Indicators
    # ============================================================

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # ---- 1h Informative data ----
        informative_1h = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="1h")
        informative_1h = self.populate_informative_1h(informative_1h, metadata)
        dataframe = merge_informative_pair(dataframe, informative_1h, self.timeframe, "1h", ffill=True)

        # ---- EMAs ----
        for period in [5, 8, 12, 13, 21, 26, 50, 100, 200]:
            dataframe[f"ema_{period}"] = ta.EMA(dataframe, timeperiod=period)

        # ---- RSI ----
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_fast"] = ta.RSI(dataframe, timeperiod=4)

        # ---- MACD ----
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]

        # ---- Bollinger Bands ----
        bb_20 = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_upper"] = bb_20["upperband"]
        dataframe["bb_lower"] = bb_20["lowerband"]
        dataframe["bb_mid"] = bb_20["middleband"]
        dataframe["bb_width"] = (dataframe["bb_upper"] - dataframe["bb_lower"]) / dataframe["bb_mid"]

        bb_40 = ta.BBANDS(dataframe, timeperiod=40, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_lower_40"] = bb_40["lowerband"]
        dataframe["bb_delta"] = (bb_20["lowerband"] - bb_40["lowerband"]).abs() / dataframe["close"]

        # ---- Volume ----
        dataframe["volume_sma_20"] = dataframe["volume"].rolling(20).mean()

        # ---- ATR ----
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # ---- MFI ----
        dataframe["mfi"] = ta.MFI(dataframe, timeperiod=14)

        # ---- Williams %R ----
        dataframe["willr"] = ta.WILLR(dataframe, timeperiod=14)

        # ---- CCI ----
        dataframe["cci"] = ta.CCI(dataframe, timeperiod=20)

        # ---- ADX ----
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)

        return dataframe

    # ============================================================
    # Entry Conditions
    # ============================================================

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions_long = []

        # ---- Condition Group 1: EMA + RSI + BB Dip Buy ----
        cond1 = (
            # Close below lower Bollinger Band (dip)
            (dataframe["close"] < dataframe["bb_lower"])
            & (dataframe["rsi"] < self.buy_rsi_slow.value)
            & (dataframe["rsi_fast"] < self.buy_rsi_fast.value)
            & (dataframe["bb_width"] > self.buy_bb_width_min.value)
            & (dataframe["bb_delta"] > self.buy_bb_delta.value)
            & (dataframe["volume"] > 0)
        )
        conditions_long.append(cond1)

        # ---- Condition Group 2: EMA Cross + MACD Confirm ----
        cond2 = (
            # EMA fast crosses above EMA slow
            (dataframe["ema_12"] > dataframe["ema_26"])
            & (dataframe["ema_12"].shift(1) <= dataframe["ema_26"].shift(1))
            & (dataframe["macdhist"] > 0)
            & (dataframe["rsi_1h"] > self.buy_rsi_1h_min.value)
            & (dataframe["volume"] > 0)
        )
        conditions_long.append(cond2)

        # ---- Condition Group 3: Deep Dip Recovery ----
        cond3 = (
            # Price well below BB lower (deep dip)
            (dataframe["close"] < dataframe["bb_lower"] * 0.99)
            & (dataframe["rsi"] < 35)
            & (dataframe["volume"] > 0)
        )
        conditions_long.append(cond3)

        # ---- Condition Group 4: Trend Continuation ----
        cond4 = (
            # Strong trend: all EMAs aligned
            (dataframe["ema_8"] > dataframe["ema_13"])
            & (dataframe["ema_13"] > dataframe["ema_21"])
            & (dataframe["close"] < dataframe["ema_8"])
            & (dataframe["close"] > dataframe["ema_13"])
            & (dataframe["volume"] > 0)
        )
        conditions_long.append(cond4)

        if conditions_long:
            dataframe.loc[reduce(lambda x, y: x | y, conditions_long), "enter_long"] = 1

        if self.can_short:
            conditions_short = []

            cond1_short = (
                (dataframe["close"] > dataframe["bb_upper"])
                & (dataframe["rsi"] > (100 - self.buy_rsi_slow.value))
                & (dataframe["rsi_fast"] > (100 - self.buy_rsi_fast.value))
                & (dataframe["bb_width"] > self.buy_bb_width_min.value)
                & (dataframe["bb_delta"] > self.buy_bb_delta.value)
                & (dataframe["volume"] > 0)
            )
            conditions_short.append(cond1_short)

            cond2_short = (
                (dataframe["ema_12"] < dataframe["ema_26"])
                & (dataframe["ema_12"].shift(1) >= dataframe["ema_26"].shift(1))
                & (dataframe["macdhist"] < 0)
                & (dataframe["rsi_1h"] < (100 - self.buy_rsi_1h_min.value))
                & (dataframe["volume"] > 0)
            )
            conditions_short.append(cond2_short)

            cond3_short = (
                (dataframe["close"] > dataframe["bb_upper"] * 1.01)
                & (dataframe["rsi"] > 65)
                & (dataframe["volume"] > 0)
            )
            conditions_short.append(cond3_short)

            cond4_short = (
                (dataframe["ema_8"] < dataframe["ema_13"])
                & (dataframe["ema_13"] < dataframe["ema_21"])
                & (dataframe["close"] > dataframe["ema_8"])
                & (dataframe["close"] < dataframe["ema_13"])
                & (dataframe["volume"] > 0)
            )
            conditions_short.append(cond4_short)

            if conditions_short:
                dataframe.loc[reduce(lambda x, y: x | y, conditions_short), "enter_short"] = 1

        return dataframe

    # ============================================================
    # Exit Conditions
    # ============================================================

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions_exit = []

        # ---- Exit 1: RSI overbought ----
        exit1 = (
            (dataframe["rsi"] > self.sell_rsi.value)
            & (dataframe["close"] > dataframe["bb_upper"])
            & (dataframe["volume"] > 0)
        )
        conditions_exit.append(exit1)

        # ---- Exit 2: MACD bearish cross ----
        if self.sell_macd_cross.value:
            exit2 = (
                (dataframe["macd"] < dataframe["macdsignal"])
                & (dataframe["macd"].shift(1) >= dataframe["macdsignal"].shift(1))
                & (dataframe["rsi"] > 60)
                & (dataframe["volume"] > 0)
            )
            conditions_exit.append(exit2)

        # ---- Exit 3: 1h trend reversal ----
        exit3 = (
            (dataframe["ema_12_1h"] < dataframe["ema_26_1h"])
            & (dataframe["ema_12_1h"].shift(1) >= dataframe["ema_26_1h"].shift(1))
            & (dataframe["rsi"] > 55)
            & (dataframe["volume"] > 0)
        )
        conditions_exit.append(exit3)

        if conditions_exit:
            dataframe.loc[reduce(lambda x, y: x | y, conditions_exit), "exit_long"] = 1

        if self.can_short:
            conditions_short_exit = []

            exit1_short = (
                (dataframe["rsi"] < (100 - self.sell_rsi.value))
                & (dataframe["close"] < dataframe["bb_lower"])
                & (dataframe["volume"] > 0)
            )
            conditions_short_exit.append(exit1_short)

            if self.sell_macd_cross.value:
                exit2_short = (
                    (dataframe["macd"] > dataframe["macdsignal"])
                    & (dataframe["macd"].shift(1) <= dataframe["macdsignal"].shift(1))
                    & (dataframe["rsi"] < 40)
                    & (dataframe["volume"] > 0)
                )
                conditions_short_exit.append(exit2_short)

            exit3_short = (
                (dataframe["ema_12_1h"] > dataframe["ema_26_1h"])
                & (dataframe["ema_12_1h"].shift(1) <= dataframe["ema_26_1h"].shift(1))
                & (dataframe["rsi"] < 45)
                & (dataframe["volume"] > 0)
            )
            conditions_short_exit.append(exit3_short)

            if conditions_short_exit:
                dataframe.loc[reduce(lambda x, y: x | y, conditions_short_exit), "exit_short"] = 1

        return dataframe

    # ============================================================
    # Custom Stoploss
    # ============================================================

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        """
        Progressive stoploss that tightens as profit increases.
        """
        if current_profit > 0.06:
            return -0.015
        elif current_profit > 0.04:
            return -0.02
        elif current_profit > 0.025:
            return -0.03
        elif current_profit > 0.015:
            return -0.04

        return self.stoploss

    # ============================================================
    # Custom Exit
    # ============================================================

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        """
        Additional custom exit logic for risk management.
        """
        # Take profit at high levels
        if current_profit > 0.08:
            return "take_profit_8pct"

        # Exit if trade is old and barely profitable
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 60
        if trade_duration > 1440 and current_profit < 0.005:
            return "stale_trade_exit"

        return None
