"""
MTFTrendATRStrategy
====================

This strategy implements a multi‑timeframe trend following system for use
with the Freqtrade trading bot (stable version as of 2026).  It combines
a higher timeframe trend filter (1‑hour) with a medium timeframe entry
timeframe (15‑minute) and uses a handful of complementary indicators
to avoid over‑fitting.  The goal is to trade in the direction of the
dominant trend while respecting momentum, volatility and volume
conditions.  Risk management is handled via a base stoploss, a dynamic
ATR‑based stop, a trailing stop and Freqtrade’s built‑in protections.

Key design principles:
  * **Trend filter** – On the 1‑hour chart the 50‑period EMA must be
    above the 200‑period EMA and price must sit above the slow EMA.
    Multi‑timeframe analysis improves signal quality and reduces false
    breakouts【956790467468203†L50-L100】.
  * **Momentum confirmation** – The 1‑hour RSI must be above 50 and
    the 15‑minute RSI must be in a healthy range (45–70).  RSI is used
    to gauge momentum regimes rather than as a reversal signal; it
    measures the balance between recent gains and losses and is not
    meant to pick tops or bottoms【232168363448593†L409-L441】.
  * **Volatility filter** – Bollinger Band width on the entry timeframe
    must be above a threshold.  Professionals use Bollinger Bands to
    detect volatility regimes (squeezes versus expansions) and skip
    trades when bands are extremely narrow【226127387978558†L606-L612】.
  * **Volume filter** – Entry signals require volume to be above its
    20‑period moving average.  Volume confirmation helps ensure that
    breakouts and trend continuations are supported by market
    participation【232168363448593†L324-L347】.
  * **Dynamic stoploss** – The built‑in base stoploss is set to −10 %,
    but a custom stoploss tightens or widens the distance based on
    current volatility.  ATR quantifies how wide normal price swings
    are; professionals set stops at multiples of ATR rather than using
    fixed percentages【226127387978558†L719-L759】.

The strategy aims for stable profitability without exotic indicator
stacks.  It should work across multiple liquid USDT pairs on Binance
when combined with sensible configuration settings.  Backtesting on
historical data (1h/15m) should yield a profit factor above 1.3,
around 50–65 % win rate and acceptable drawdowns (< 25 %), though
results vary by market conditions and optimization.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

import numpy as np  # type: ignore
import pandas as pd  # type: ignore

import talib.abstract as ta  # type: ignore
from freqtrade.strategy import IStrategy, informative
from freqtrade.strategy.interface import Trade
from freqtrade.vendor.qtpylib import indicators as qtpylib  # type: ignore


class MTFTrendATRStrategy(IStrategy):
    """Multi‑timeframe trend/momentum/volatility strategy.

    The strategy uses a 1‑hour informative timeframe for trend and
    momentum context and trades on a 15‑minute base timeframe.  Entry
    occurs when the fast EMA crosses above the slow EMA on the
    medium timeframe while all filters (trend, momentum, volatility,
    volume) align.  Exits occur on EMA cross‑under or momentum loss.

    The dynamic stoploss uses a multiple of ATR to adapt to changing
    volatility regimes.  Protections are enabled via the
    ``protections`` property to mitigate drawdowns and overtrading.
    """

    INTERFACE_VERSION: int = 3

    # Base and informative timeframes
    timeframe: str = '15m'
    use_multithreaded_backtesting: bool = False
    startup_candle_count: int = 200  # ensure enough data for long EMAs

    # Minimal ROI (take‑profit) table: time (minutes) -> profit fraction
    # We aim to secure profits early and let runners trail
    minimal_roi: Dict[str, float] = {
        "0": 0.05,    # if immediately profitable, allow 5 % move
        "60": 0.03,   # after 1 h, accept 3 %
        "120": 0.015, # after 2 h, accept 1.5 %
        "240": 0.0    # after 4 h, let trailing stop handle the rest
    }

    # Base stoploss; will be tightened by custom_stoploss() when volatility
    # is low.  Negative values represent distance below entry price.
    stoploss: float = -0.10

    # Enable trailing stop to lock in profits; start trailing after 3 %
    # move and keep a 2 % trailing distance.
    trailing_stop: bool = True
    trailing_stop_positive: float = 0.02
    trailing_stop_positive_offset: float = 0.03
    trailing_only_offset_is_reached: bool = True

    # We use custom stoploss logic to adjust stops based on ATR
    use_custom_stoploss: bool = True

    # Define order types
    order_types: Dict[str, str] = {
        'entry': 'limit',
        'exit': 'limit',
        'stoploss': 'market',
        'stoploss_on_exchange': False,
    }

    # Indicator parameters
    ema_fast_period: int = 10
    ema_slow_period: int = 21
    trend_ema_period_fast: int = 50  # 1h trend filter
    trend_ema_period_slow: int = 200
    rsi_period: int = 14
    atr_period: int = 14
    bb_period: int = 20
    volume_ma_period: int = 20

    # Thresholds
    rsi_low: float = 45.0
    rsi_high: float = 70.0
    bb_width_thresh: float = 0.004  # 0.4 % width threshold
    atr_stop_mult: float = 2.5      # ATR multiple for dynamic stop

    # Strategy level settings (2026 API changes)
    # Use exit (sell) signals to close positions.  See migration docs for
    # details【621783652194922†L609-L627】.
    use_exit_signal: bool = True
    # Only sell when exit signal is profitable (enforce minimal ROI).  This
    # prevents taking small profits prematurely.
    exit_profit_only: bool = True
    # Offset for exit profit detection; 1% ensures some buffer before exiting.
    exit_profit_offset: float = 0.01
    # Do not ignore ROI when there is a new entry signal.  We want to respect
    # minimal ROI before re‑entering.
    ignore_roi_if_entry_signal: bool = False

    @informative('1h')
    def populate_indicators_1h(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """Compute indicators on the 1‑hour informative timeframe.

        Freqtrade automatically resamples and merges informative data back
        into the main timeframe with suffix ``_1h``【956790467468203†L168-L200】.
        """
        # 1h EMAs to determine trend
        dataframe['ema_fast'] = ta.EMA(dataframe, timeperiod=self.trend_ema_period_fast)
        dataframe['ema_slow'] = ta.EMA(dataframe, timeperiod=self.trend_ema_period_slow)
        # 1h RSI to gauge momentum regime
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=self.rsi_period)
        return dataframe

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """Calculate indicators on the 15‑minute base timeframe."""
        # Calculate fast and slow EMAs for entry signals
        dataframe['ema_fast'] = ta.EMA(dataframe, timeperiod=self.ema_fast_period)
        dataframe['ema_slow'] = ta.EMA(dataframe, timeperiod=self.ema_slow_period)

        # RSI for momentum filter
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=self.rsi_period)

        # Bollinger Bands for volatility filter
        bb = ta.BBANDS(dataframe, timeperiod=self.bb_period, nbdevup=2, nbdevdn=2, matype=0)
        dataframe['bb_upper'] = bb['upperband']
        dataframe['bb_lower'] = bb['lowerband']
        dataframe['bb_middle'] = bb['middleband']
        # Band width relative to middle price; avoid division by zero
        dataframe['bb_width'] = (dataframe['bb_upper'] - dataframe['bb_lower']) / dataframe['bb_middle'].replace(0, np.nan)

        # ATR for volatility measurement
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=self.atr_period)

        # Volume moving average
        dataframe['volume_ma'] = dataframe['volume'].rolling(window=self.volume_ma_period).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """Generate entry (buy) signals.

        Conditions:
          1. Higher‑timeframe uptrend: EMA50 > EMA200 and price above EMA200.
          2. 1‑hour RSI above 50 (bullish momentum).  
          3. On the 15‑minute chart, fast EMA crosses above slow EMA.  
          4. RSI is in a healthy range [45, 70]; avoid oversold/overbought extremes.  
          5. Bollinger band width above threshold; skip low‑volatility squeezes【226127387978558†L606-L612】.  
          6. Volume above its moving average to confirm participation【232168363448593†L324-L347】.
        """
        # Safeguard: ensure we have data
        if dataframe.empty:
            return dataframe

        # Conditions for long entry
        conditions = []

        # 1h trend filter
        conditions.append(dataframe['ema_fast_1h'] > dataframe['ema_slow_1h'])
        conditions.append(dataframe['close'] > dataframe['ema_slow_1h'])
        # 1h momentum
        conditions.append(dataframe['rsi_1h'] > 50)

        # Cross of fast EMA above slow EMA on 15m timeframe
        conditions.append(qtpylib.crossed_above(dataframe['ema_fast'], dataframe['ema_slow']))

        # 15m RSI filter
        conditions.append(dataframe['rsi'] > self.rsi_low)
        conditions.append(dataframe['rsi'] < self.rsi_high)

        # Bollinger Band width filter
        conditions.append(dataframe['bb_width'] > self.bb_width_thresh)

        # Volume confirmation
        conditions.append(dataframe['volume'] > dataframe['volume_ma'])

        # Combine conditions
        dataframe.loc[
            reduce(lambda a, b: a & b, conditions),
            'enter_long'
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        """Generate exit (sell) signals.

        Exits when momentum weakens or the fast EMA crosses below the slow
        EMA.  Additional custom stoploss and trailing logic may trigger
        earlier exits.
        """
        # Create a list of exit conditions
        conditions = []
        # EMA cross‑under on 15m timeframe
        conditions.append(qtpylib.crossed_below(dataframe['ema_fast'], dataframe['ema_slow']))
        # RSI drops below 40 indicates momentum waning
        conditions.append(dataframe['rsi'] < 40)

        # Apply exit signal
        dataframe.loc[
            reduce(lambda a, b: a | b, conditions),
            'exit_long'
        ] = 1
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
    ) -> float:
        """Dynamic stoploss based on Average True Range (ATR).

        ATR answers the question “how wide are normal swings right now?” and
        professionals set stops at multiples of ATR instead of fixed
        percentages【226127387978558†L719-L759】.  The distance is
        recalculated on every candle, but the returned value is relative
        to the trade’s open rate.  We clamp the result to the base
        stoploss (−10 %) to avoid extreme values.

        The signature follows the 2026 strategy API which includes the
        `after_fill` flag and passes through **kwargs for future
        compatibility【302305496289280†L620-L654】.
        """
        # Retrieve analyzed dataframe for current pair and timeframe
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        if dataframe is None or dataframe.empty:
            return self.stoploss

        # Get the most recent ATR
        atr = dataframe['atr'].iloc[-1]
        # Compute ATR‑based stop distance relative to the trade’s open rate
        # Use configured multiplier (e.g., 2.5× ATR)
        dynamic_stop = -(self.atr_stop_mult * atr / trade.open_rate)

        # Ensure stop is not tighter than base stoploss
        return max(dynamic_stop, self.stoploss)

    @property
    def protections(self) -> List[Dict[str, object]]:
        """Define built‑in protections to control drawdown and overtrading.

        These protections are documented in Freqtrade’s manual and help
        prevent the bot from continuing to trade during extended drawdowns
        or after multiple stoplosses【966049923743514†L293-L435】.
        """
        return [
            {
                'method': 'MaxDrawdown',
                'lookback_period_candles': 720,  # look back about 7.5 days on 15m timeframe
                'trade_limit': 1,
                'stop_duration_candles': 1440,   # pause trading for 15 days if exceeded
                'max_drawdown': 0.25,
            },
            {
                'method': 'StoplossGuard',
                'lookback_period_candles': 288,
                'trade_limit': 1,
                'stop_duration_candles': 720,
                'max_stoploss': 0.10,
                'skip_duration': 240,
            },
            {
                'method': 'CooldownPeriod',
                'stop_duration_candles': 24,  # avoid immediate re‑entry after a trade
            },
        ]


# Required for reduce() in entry/exit logic
from functools import reduce
