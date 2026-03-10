from freqtrade.strategy import IStrategy, informative, IntParameter, DecimalParameter, CategoricalParameter
from pandas import DataFrame
import talib.abstract as ta
import numpy as np

class HybridNfiRpbVol_v1(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short = False
    startup_candle_count = 240

    minimal_roi = {
        "0": 0.03,
        "30": 0.02,
        "90": 0.01,
        "240": 0
    }

    stoploss = -0.09
    trailing_stop = True
    trailing_stop_positive = 0.012
    trailing_stop_positive_offset = 0.025
    trailing_only_offset_is_reached = True

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Hyperopt Spaces
    buy_vwap_diff = DecimalParameter(0.970, 1.000, default=0.973, space="buy", optimize=True)
    buy_macd_hist = DecimalParameter(0.0, 10.0, default=2.809, space="buy", optimize=True)

    sell_macd_cross = CategoricalParameter([True, False], default=True, space="sell", optimize=True)

    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # MACD
        macd = ta.MACD(dataframe)
        dataframe['macd'] = macd['macd']
        dataframe['macdsignal'] = macd['macdsignal']
        dataframe['macdhist'] = macd['macdhist']

        # VWAP (rolling 24 hours of 5m candles = 288 periods)
        typical_price = (dataframe['high'] + dataframe['low'] + dataframe['close']) / 3
        dataframe['vwap'] = (dataframe['volume'] * typical_price).rolling(window=288).sum() / dataframe['volume'].rolling(window=288).sum()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["close_1h"] > dataframe["ema_200_1h"]) &
                (dataframe["close"] < dataframe["vwap"] * self.buy_vwap_diff.value) &
                (dataframe["macdhist"] > self.buy_macd_hist.value) &
                (dataframe["macd"] > dataframe["macdsignal"]) &
                (dataframe["volume"] > 0)
            ),
            ["enter_long", "enter_tag"]
        ] = (1, "vwap_macd_buy")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (self.sell_macd_cross.value == True) &
                (dataframe["macd"] < dataframe["macdsignal"])
            ),
            ["exit_long", "exit_tag"]
        ] = (1, "macd_cross_sell")
        return dataframe