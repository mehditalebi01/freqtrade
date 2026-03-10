import json
import logging
import pprint
import sys
import subprocess
from pathlib import Path
from types import SimpleNamespace
import attr

REPO_ROOT = Path(__file__).parent.parent.parent

log = logging.getLogger(__name__)

@attr.s(frozen=True)
class ProcessResult:
    exitcode = attr.ib()
    stdout = attr.ib()
    stderr = attr.ib()
    cmdline = attr.ib(default=None, kw_only=True)

    @exitcode.validator
    def _validate_exitcode(self, attribute, value):
        if not isinstance(value, int):
            raise ValueError(f"'exitcode' needs to be an integer, not '{type(value)}'")

    def __str__(self):
        message = self.__class__.__name__
        if self.cmdline:
            message += f"\n Command Line: {self.cmdline}"
        if self.exitcode is not None:
            message += f"\n Exitcode: {self.exitcode}"
        if self.stdout or self.stderr:
            message += "\n Process Output:"
        if self.stdout:
            message += f"\n   >>>>> STDOUT >>>>>\n{self.stdout}\n   <<<<< STDOUT <<<<<"
        if self.stderr:
            message += f"\n   >>>>> STDERR >>>>>\n{self.stderr}\n   <<<<< STDERR <<<<<"
        return message + "\n"

class Backtest:
    def __init__(self, request, exchange=None, trading_mode=None):
        self.request = request
        self.exchange = exchange
        self.trading_mode = trading_mode

    def __call__(self, start_date, end_date, exchange=None, trading_mode=None):
        if exchange is None:
            exchange = self.exchange
        if exchange is None:
            raise RuntimeError("No 'exchange' was passed")

        # Fallback to local paths
        export_dir = REPO_ROOT / "user_data" / "backtest_results"
        export_dir.mkdir(parents=True, exist_ok=True)
        json_filename = f"bt-result-{exchange}-{trading_mode}-{start_date}-{end_date}.json"

        # Cmdline
        cmdline = [
            f"{sys.executable}",
            "-m", "freqtrade", "backtesting",
            "--strategy=NfiInspiredStrategy",
            f"--timerange={start_date}-{end_date}",
            "--user-data-dir=user_data",
            "--config=user_data/config_nfi_futures.json",
            "--export=none",
            f"--export-directory={str(export_dir)}",
            f"--export-filename={json_filename}",
        ]

        log.info("Running cmdline '%s' on '%s'", " ".join(cmdline), REPO_ROOT)
        proc = subprocess.run(cmdline, check=False, shell=False, cwd=REPO_ROOT, text=True, capture_output=True)
        ret = ProcessResult(
            exitcode=proc.returncode,
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
            cmdline=cmdline,
        )
        if ret.exitcode != 0:
            raise RuntimeError(f"Backtest failed with exit code {ret.exitcode}!\n{ret}")

        # Collect artifacts
        json_results_file = export_dir / json_filename
        if not json_results_file.exists():
            # If freqtrade didn't write it exactly as expected, try to find the latest
            files = list(export_dir.glob("*.json"))
            if not files:
                raise FileNotFoundError(f"No results JSON found in {export_dir}")
            json_results_file = sorted(files, key=lambda x: x.stat().st_mtime)[-1]

        raw_text = json_results_file.read_text()
        if not raw_text.strip():
            raise ValueError("Backtest result JSON is empty.")
        results_data = json.loads(raw_text)

        backtest_results = BacktestResults(
            stdout=ret.stdout.strip(),
            stderr=ret.stderr.strip(),
            raw_data=results_data,
        )

        return backtest_results

@attr.s(frozen=True)
class BacktestResults:
    stdout: str = attr.ib(repr=False)
    stderr: str = attr.ib(repr=False)
    raw_data: dict = attr.ib(repr=False)
    _results: dict = attr.ib(init=False, repr=False)
    _stats: dict = attr.ib(init=False, repr=False)
    results: SimpleNamespace = attr.ib(init=False, repr=False)
    full_stats: SimpleNamespace = attr.ib(init=False, repr=False)
    _stats_pct: dict = attr.ib(init=False, repr=False)
    stats_pct: SimpleNamespace = attr.ib(init=False, repr=True)

    @_results.default
    def _set_results(self):
        strategy_data = self.raw_data.get("strategy")
        if isinstance(strategy_data, dict):
            return strategy_data.get("NfiInspiredStrategy", {})
        elif isinstance(strategy_data, str):
            return self.raw_data.get("NfiInspiredStrategy", {})
        else:
            return {}

    @_stats.default
    def _set_stats(self):
        comparison_data = self.raw_data.get("strategy_comparison")
        if isinstance(comparison_data, list) and len(comparison_data) > 0:
            return comparison_data[0]
        elif isinstance(comparison_data, dict):
            # Sometimes freqtrade returns a dict with strategy names as keys
            return comparison_data.get("NfiInspiredStrategy", comparison_data)
        return {}

    @results.default
    def _set_results_ns(self):
        return json.loads(json.dumps(self._results), object_hook=lambda d: SimpleNamespace(**d))

    @full_stats.default
    def _set_full_stats_ns(self):
        return json.loads(json.dumps(self._stats), object_hook=lambda d: SimpleNamespace(**d))

    @_stats_pct.default
    def _set_stats_pct_dict(self):
        trades = getattr(self.full_stats, 'trades', 0)
        wins = getattr(self.full_stats, 'wins', 0)
        return {
            "max_drawdown": getattr(self.results, 'max_drawdown_account', 0) * 100,
            "trades": trades,
            "winrate": round(wins * 100.0 / trades, 2) if trades > 0 else 0,
        }

    @stats_pct.default
    def _set_stats_pct_ns(self):
        return json.loads(json.dumps(self._stats_pct), object_hook=lambda d: SimpleNamespace(**d))

@attr.s(frozen=True)
class Timerange:
    start_date = attr.ib()
    end_date = attr.ib()

@attr.s(frozen=True)
class Exchange:
    name = attr.ib()
    winrate = attr.ib()
    max_drawdown = attr.ib()
