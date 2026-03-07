"""
MTFTrendATRStrategy Final — Long & Short with Hyperopt Parameters
=================================================================
"""

from __future__ import annotations
from datetime import datetime
from functools import reduce
from typing import Dict, List

import numpy as np  # type: ignore
import pandas as pd  # type: ignore
import talib.abstract as ta  # type: ignore

from freqtrade.strategy import (
    IStrategy,
    informative,
    DecimalParameter,
)
from freqtrade.strategy.interface import Trade
from freqtrade.vendor.qtpylib import indicators as qtpylib  # type: ignore


class MTFTrendATRStrategy(IStrategy):
    """Multi-timeframe trend strategy — long & short optimized."""

    INTERFACE_VERSION: int = 3
    can_short: bool = True  # enable short entries

    timeframe: str = '15m'
    startup_candle_count: int = 200
    process_only_new_candles: bool = True

    # ================================================================
    # OPTIMIZED HYPEROPT PARAMETERS (Epoch 385/500) | Best performance
    # ================================================================
    entry_adx = DecimalParameter(15.0, 30.0, default=21.0, decimals=0, space='buy', optimize=True, load=True)
    entry_rsi_long_low = DecimalParameter(40.0, 55.0, default=51.0, decimals=0, space='buy', optimize=True, load=True)
    entry_rsi_short_high = DecimalParameter(45.0, 60.0, default=60.0, decimals=0, space='buy', optimize=True, load=True)
    entry_volume_mult = DecimalParameter(1.0, 2.0, default=1.8, decimals=1, space='buy', optimize=True, load=True)
    entry_bb_width = DecimalParameter(0.002, 0.008, default=0.002, decimals=3, space='buy', optimize=True, load=True)

    exit_rsi_long = DecimalParameter(30.0, 50.0, default=47.0, decimals=0, space='sell', optimize=True, load=True)
    exit_rsi_short = DecimalParameter(55.0, 75.0, default=66.0, decimals=0, space='sell', optimize=True, load=True)

    # ── ROI ──────────────────────────────────────────────────────────
    minimal_roi: Dict[str, float] = {
        "0": 0.102,
        "78": 0.056,
        "240": 0.015,
        "548": 0.0
    }

    # ── Stoploss ─────────────────────────────────────────────────────
    stoploss: float = -0.337 # Optimized very wide stoploss to let ROI handle it
    use_custom_stoploss: bool = True
    trailing_stop: bool = False

    # ── Order types ──────────────────────────────────────────────────
    order_types: Dict[str, str] = {
        'entry':  'limit',
        'exit':   'limit',
        'stoploss': 'market',
        'stoploss_on_exchange': False,
    }

    # ── Fixed indicator parameters ───────────────────────────────────
    ema_fast_period:       int = 12
    ema_slow_period:       int = 26
    trend_ema_period_fast: int = 50
    trend_ema_period_slow: int = 200
    rsi_period:            int = 14
    atr_period:            int = 14
    bb_period:             int = 20
    volume_ma_period:      int = 20
    adx_period:            int = 14
    macd_fast:             int = 12
    macd_slow:             int = 26
    macd_signal:           int = 9

    # Fixed boundaries
    rsi_high_long:  float = 72.0   
    rsi_low_short:  float = 28.0   

    use_exit_signal:            bool  = True
    exit_profit_only:           bool  = False
    exit_profit_offset:         float = 0.0
    ignore_roi_if_entry_signal: bool  = False

    @informative('1h')
    def populate_indicators_1h(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe['ema_fast'] = ta.EMA(dataframe, timeperiod=self.trend_ema_period_fast)
        dataframe['ema_slow'] = ta.EMA(dataframe, timeperiod=self.trend_ema_period_slow)
        dataframe['rsi']      = ta.RSI(dataframe, timeperiod=self.rsi_period)
        dataframe['adx']      = ta.ADX(dataframe, timeperiod=self.adx_period)
        return dataframe

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe['ema_fast'] = ta.EMA(dataframe, timeperiod=self.ema_fast_period)
        dataframe['ema_slow'] = ta.EMA(dataframe, timeperiod=self.ema_slow_period)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=self.rsi_period)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=self.adx_period)

        macd = ta.MACD(dataframe, fastperiod=self.macd_fast, slowperiod=self.macd_slow, signalperiod=self.macd_signal)
        dataframe['macd']        = macd['macd']
        dataframe['macd_signal'] = macd['macdsignal']
        dataframe['macd_hist']   = macd['macdhist']

        bb = ta.BBANDS(dataframe, timeperiod=self.bb_period, nbdevup=2.0, nbdevdn=2.0, matype=0)
        dataframe['bb_upper']  = bb['upperband']
        dataframe['bb_lower']  = bb['lowerband']
        dataframe['bb_middle'] = bb['middleband']
        dataframe['bb_width']  = (dataframe['bb_upper'] - dataframe['bb_lower']) / dataframe['bb_middle'].replace(0, np.nan)

        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        if dataframe.empty: return dataframe

        volume_ma = dataframe['volume'].rolling(window=self.volume_ma_period).mean()

        lc = []
        lc.append(dataframe['ema_fast_1h'] > dataframe['ema_slow_1h'])
        lc.append(dataframe['close'] > dataframe['ema_slow_1h'])
        lc.append(dataframe['rsi_1h'] > 50)
        lc.append(dataframe['adx_1h'] > 25)  # Strict trending requirement for longs in a bear market
        lc.append(qtpylib.crossed_above(dataframe['ema_fast'], dataframe['ema_slow']))
        lc.append(dataframe['rsi'] > self.entry_rsi_long_low.value)
        lc.append(dataframe['rsi'] < self.rsi_high_long)
        lc.append(dataframe['adx'] > self.entry_adx.value)
        lc.append(dataframe['macd_hist'] > 0)
        lc.append(dataframe['bb_width'] > self.entry_bb_width.value)
        lc.append(dataframe['volume'] > (volume_ma * self.entry_volume_mult.value))
        dataframe.loc[reduce(lambda a, b: a & b, lc), 'enter_long'] = 1

        sc = []
        sc.append(dataframe['ema_fast_1h'] < dataframe['ema_slow_1h'])
        sc.append(dataframe['close'] < dataframe['ema_slow_1h'])
        sc.append(dataframe['rsi_1h'] < 50)
        sc.append(qtpylib.crossed_below(dataframe['ema_fast'], dataframe['ema_slow']))
        sc.append(dataframe['rsi'] < self.entry_rsi_short_high.value)
        sc.append(dataframe['rsi'] > self.rsi_low_short)
        sc.append(dataframe['adx'] > self.entry_adx.value)
        sc.append(dataframe['macd_hist'] < 0)
        sc.append(dataframe['bb_width'] > self.entry_bb_width.value)
        sc.append(dataframe['volume'] > (volume_ma * self.entry_volume_mult.value))
        dataframe.loc[reduce(lambda a, b: a & b, sc), 'enter_short'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        if dataframe.empty: return dataframe

        long_trend_reversal = (
            (dataframe['ema_fast_1h'] < dataframe['ema_slow_1h'])
            & (dataframe['close'] < dataframe['ema_slow_1h'])
            & (dataframe['rsi'] < self.exit_rsi_long.value)
        )
        dataframe.loc[long_trend_reversal, 'exit_long'] = 1

        short_trend_reversal = (
            (dataframe['ema_fast_1h'] > dataframe['ema_slow_1h'])
            & (dataframe['close'] > dataframe['ema_slow_1h'])
            & (dataframe['rsi'] > self.exit_rsi_short.value)
        )
        dataframe.loc[short_trend_reversal, 'exit_short'] = 1

        return dataframe

    def custom_stoploss(
        self, pair: str, trade: Trade, current_time: datetime,
        current_rate: float, current_profit: float, after_fill: bool, **kwargs,
    ) -> float:
        """Conservative step-down stoploss — only intervenes at high profit."""
        if current_profit >= 0.10:
            return -0.03
        if current_profit >= 0.05:
            return -0.02
        return self.stoploss

    @property
    def protections(self) -> List[Dict[str, object]]:
        return [
            {
                'method': 'MaxDrawdown',
                'lookback_period_candles': 720,
                'trade_limit': 1,
                'stop_duration_candles': 288,
                'max_drawdown': 0.20,
            },
            {
                'method': 'StoplossGuard',
                'lookback_period_candles': 288,
                'trade_limit': 4,
                'stop_duration_candles': 96,
                'max_stoploss': 0.05,
            },
            {
                'method': 'CooldownPeriod',
                'stop_duration_candles': 4,
            },
        ]
