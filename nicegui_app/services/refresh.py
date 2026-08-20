from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any,Callable

from nicegui_app.services.calculations import metrics_frame
from nicegui_app.services.portfolio_data import HISTORY_DB_PATH,PORTFOLIO_DB_PATH,load_history,load_holdings

Fingerprint=tuple[int,int]


def database_fingerprint(path:Path)->Fingerprint:
    stat=path.stat();return (stat.st_mtime_ns,stat.st_size)


@dataclass
class RefreshResult:
    portfolio_changed:bool;history_changed:bool;holdings:list[dict[str,Any]]|None=None;metrics:Any=None;history:list[dict[str,Any]]|None=None


class ReadOnlyRefreshCoordinator:
    def __init__(self,portfolio_path:Path=PORTFOLIO_DB_PATH,history_path:Path=HISTORY_DB_PATH,holdings_loader:Callable=load_holdings,history_loader:Callable=load_history):
        self.portfolio_path=portfolio_path;self.history_path=history_path;self.holdings_loader=holdings_loader;self.history_loader=history_loader;self.portfolio_fingerprint:Fingerprint|None=None;self.history_fingerprint:Fingerprint|None=None
    def refresh(self,force:bool=False)->RefreshResult:
        portfolio_fp=database_fingerprint(self.portfolio_path);history_fp=database_fingerprint(self.history_path);portfolio_changed=force or portfolio_fp!=self.portfolio_fingerprint;history_changed=force or history_fp!=self.history_fingerprint
        holdings=self.holdings_loader(self.portfolio_path) if portfolio_changed else None;history=self.history_loader(self.history_path) if history_changed else None
        self.portfolio_fingerprint=portfolio_fp;self.history_fingerprint=history_fp
        return RefreshResult(portfolio_changed,history_changed,holdings,metrics_frame(holdings) if holdings is not None else None,history)
