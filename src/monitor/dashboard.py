"""FastAPI web dashboard for Copywallet monitoring.

Provides a read-only overview of portfolio state, open positions,
recent trades, PnL charts, leader wallets, and configuration.
All data is pulled from the shared :class:`Database` and executor.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import DashboardConfig
from src.executor.paper import PaperExecutor
from src.state.database import Database

logger = logging.getLogger(__name__)


class Dashboard:
    """Read-only FastAPI dashboard wired to the bot's database and executor.

    Args:
        db: Connected :class:`Database` instance.
        executor: :class:`PaperExecutor` for balance queries.
        config: :class:`DashboardConfig` with host/port/password.
    """

    def __init__(
        self,
        db: Database,
        executor: PaperExecutor,
        config: DashboardConfig,
    ) -> None:
        self.app = FastAPI(title="Copywallet Dashboard")
        self._db = db
        self._executor = executor
        self._config = config

        self.app.mount("/static", StaticFiles(directory="static"), name="static")
        self._templates = Jinja2Templates(directory="src/templates")

        self._register_routes()

    # ------------------------------------------------------------------
    # Route registration
    # ------------------------------------------------------------------

    def _register_routes(self) -> None:
        """Bind all GET endpoints to the FastAPI application."""

        @self.app.get("/", response_class=RedirectResponse)
        async def root() -> str:
            """Redirect bare root to the overview page."""
            return "/overview"

        @self.app.get("/overview", response_class=HTMLResponse)
        async def overview(request: Request) -> HTMLResponse:
            """Dashboard landing page with key stats and recent activity."""
            balance = await self._executor.get_balance()
            positions = await self._db.get_open_positions()
            trades = await self._db.get_recent_trades(10)
            portfolio = await self._db.fetch_all(
                "SELECT * FROM portfolio ORDER BY timestamp DESC LIMIT 1"
            )

            latest: Dict[str, Any] = portfolio[0] if portfolio else {}

            return self._templates.TemplateResponse(
                "overview.html",
                {
                    "request": request,
                    "balance": balance,
                    "positions_count": len(positions),
                    "recent_trades": trades[:5],
                    "portfolio": latest,
                },
            )

        @self.app.get("/positions", response_class=HTMLResponse)
        async def positions(request: Request) -> HTMLResponse:
            """Table of all currently open positions."""
            rows = await self._db.get_open_positions()
            return self._templates.TemplateResponse(
                "positions.html",
                {"request": request, "positions": rows},
            )

        @self.app.get("/trades", response_class=HTMLResponse)
        async def trades(request: Request) -> HTMLResponse:
            """Chronological table of the 50 most recent trades."""
            rows = await self._db.get_recent_trades(50)
            return self._templates.TemplateResponse(
                "trades.html",
                {"request": request, "trades": rows},
            )

        @self.app.get("/pnl", response_class=HTMLResponse)
        async def pnl(request: Request) -> HTMLResponse:
            """Portfolio value and daily PnL charts (Chart.js)."""
            snapshots: List[Dict[str, Any]] = await self._db.fetch_all(
                "SELECT * FROM portfolio ORDER BY timestamp ASC"
            )
            return self._templates.TemplateResponse(
                "pnl.html",
                {"request": request, "snapshots": snapshots},
            )

        @self.app.get("/leaders", response_class=HTMLResponse)
        async def leaders(request: Request) -> HTMLResponse:
            """Leader wallet profiles with category breakdowns."""
            wallets = await self._db.get_tracked_wallets()
            return self._templates.TemplateResponse(
                "leaders.html",
                {"request": request, "wallets": wallets},
            )

        @self.app.get("/settings", response_class=HTMLResponse)
        async def settings(request: Request) -> HTMLResponse:
            """Read-only display of current bot configuration."""
            return self._templates.TemplateResponse(
                "settings.html",
                {"request": request, "mode": "paper"},
            )

        @self.app.get("/api/portfolio", response_class=JSONResponse)
        async def api_portfolio() -> List[Dict[str, Any]]:
            """JSON endpoint consumed by Chart.js on the PnL page."""
            return await self._db.fetch_all(
                "SELECT timestamp, total_value, daily_pnl "
                "FROM portfolio ORDER BY timestamp ASC"
            )
