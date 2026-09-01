from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any,Callable

import math

import pandas as pd

from nicegui_app.services.calculations import metrics_frame
from nicegui_app.services.market_refresh_lock import MARKET_REFRESH_LOCK
from nicegui_app.services.portfolio_data import HISTORY_DB_PATH,PORTFOLIO_DB_PATH,load_history,load_holdings
from shared_data_preparation import persist_live_snapshot_if_ready

Fingerprint=tuple[int,int]


def _positive_number(value:Any)->bool:
    try:
        number=float(value)
    except (TypeError,ValueError):
        return False
    return math.isfinite(number) and number>0


def database_fingerprint(path:Path)->Fingerprint:
    stat=path.stat();return (stat.st_mtime_ns,stat.st_size)


@dataclass
class RefreshResult:
    portfolio_changed:bool
    history_changed:bool
    metrics_changed:bool=False
    holdings:list[dict[str,Any]]|None=None
    metrics:Any=None
    history:list[dict[str,Any]]|None=None
    live_snapshot_changed:bool=False


class ReadOnlyRefreshCoordinator:
    """Coordinate one coherent Yahoo, calculation, and live-history refresh.

    NiceGUI clients, the server market worker, and explicit preparation can all
    request refreshes. The process-wide reentrant lock prevents overlapping
    callers from multiplying Yahoo requests or publishing mismatched holdings,
    metrics, and snapshots. A forced refresh deliberately reloads persisted
    holdings after a write even when a filesystem fingerprint has not changed.
    """
    def __init__(self,portfolio_path:Path=PORTFOLIO_DB_PATH,history_path:Path=HISTORY_DB_PATH,holdings_loader:Callable=load_holdings,history_loader:Callable=load_history,market_loader:Callable|None=None,snapshot_persister:Callable=persist_live_snapshot_if_ready):
        self.portfolio_path=portfolio_path;self.history_path=history_path;self.holdings_loader=holdings_loader;self.history_loader=history_loader;self.market_loader=market_loader;self.portfolio_fingerprint:Fingerprint|None=None;self.history_fingerprint:Fingerprint|None=None
        self.snapshot_persister=snapshot_persister
        self._holdings:list[dict[str,Any]]=[]
        self._metrics:pd.DataFrame|None=None
    def refresh(self,force:bool=False)->RefreshResult:
        with MARKET_REFRESH_LOCK:
            return self._refresh_locked(force)

    def _refresh_locked(self,force:bool=False)->RefreshResult:
        portfolio_fp=database_fingerprint(self.portfolio_path);history_fp=database_fingerprint(self.history_path);portfolio_changed=force or portfolio_fp!=self.portfolio_fingerprint;history_changed=force or history_fp!=self.history_fingerprint
        holdings=self.holdings_loader(self.portfolio_path) if portfolio_changed else None
        if holdings is not None:self._holdings=holdings
        self.portfolio_fingerprint=portfolio_fp
        prices,yields=(None,None)
        if self.market_loader and self._holdings:
            prices,yields=self.market_loader([row.get("ticker","") for row in self._holdings])
        priced_tickers={str(row.get("ticker","")).strip().upper() for row in self._holdings if _positive_number(row.get("shares",0))}
        valid_live_prices={str(ticker).strip().upper() for ticker,value in (prices or {}).items() if _positive_number(value)}
        market_data_complete=not self.market_loader or not priced_tickers or priced_tickers.issubset(valid_live_prices)
        # A partial Yahoo response must not replace a previously coherent metric
        # set with zero-priced holdings or persist a corrupt live snapshot.
        if not market_data_complete and self._metrics is not None:
            recalculated=self._metrics
        else:
            recalculated=metrics_frame(self._holdings,prices,yields) if self._holdings else pd.DataFrame()
        metrics_changed=self._metrics is None or not recalculated.equals(self._metrics)
        self._metrics=recalculated
        live_snapshot_changed=False
        # Live history advances from current calculations without waiting for a
        # new brokerage statement, but only when every priced holding is usable.
        snapshot_attempted=bool(self.market_loader and priced_tickers and market_data_complete)
        if snapshot_attempted:
            live_snapshot_changed=self.snapshot_persister(recalculated,self.history_path)=="Updated"
        final_history_fp=database_fingerprint(self.history_path) if snapshot_attempted else history_fp
        history_changed=history_changed or live_snapshot_changed
        history=self.history_loader(self.history_path) if history_changed else None
        self.history_fingerprint=final_history_fp
        return RefreshResult(portfolio_changed,history_changed,metrics_changed,holdings,recalculated,history,live_snapshot_changed)
