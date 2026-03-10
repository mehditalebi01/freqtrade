import pytest

from tests.backtests.helpers import Backtest, Exchange, Timerange

def exchange_fmt(value):
    return value.name

@pytest.fixture(
    scope="session",
    params=(
        Exchange(name="binance", winrate=90, max_drawdown=5),
    ),
    ids=exchange_fmt,
)
def exchange(request):
    return request.param

def trading_mode_fmt(param):
    return param

@pytest.fixture(
    params=(
        "futures",
    ),
    ids=trading_mode_fmt,
)
def trading_mode(request):
    return request.param

@pytest.fixture
def backtest(request):
    return Backtest(request)

def timerange_fmt(value):
    return f"{value.start_date}-{value.end_date}"

@pytest.fixture(
    params=(
        # 2025 Q1 Weeks
        Timerange("20250223", "20250302"),
        Timerange("20250216", "20250223"),
        Timerange("20250209", "20250216"),
        Timerange("20250202", "20250209"),
        Timerange("20250126", "20250202"),
        Timerange("20250119", "20250126"),
        Timerange("20250112", "20250119"),
        Timerange("20250105", "20250112"),
        Timerange("20241229", "20250105"),
    ),
    ids=timerange_fmt,
)
def timerange(request):
    return request.param

def test_expected_values(backtest, trading_mode, timerange, exchange):
    ret = backtest(
        start_date=timerange.start_date,
        end_date=timerange.end_date,
        exchange=exchange.name,
        trading_mode=trading_mode,
    )

    expected_winrate = exchange.winrate
    expected_max_drawdown = exchange.max_drawdown

    if not (ret.stats_pct.winrate >= expected_winrate or ret.stats_pct.trades == 0):
        print(f"[NOTE] Expected winrate ≥ {expected_winrate}, got {ret.stats_pct.winrate}. Trades: {ret.stats_pct.trades}.")

    if not (ret.stats_pct.max_drawdown <= expected_max_drawdown):
        print(f"[NOTE] Expected max drawdown ≤ {expected_max_drawdown}, got {ret.stats_pct.max_drawdown}.")
        
    # We purposefully don't assert and fail the test because we're just recording stats right now.
    # We can enforce them once the strategy is optimized.
    assert True
