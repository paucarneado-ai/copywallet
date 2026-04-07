"""Telegram bot for trade alerts and interactive commands.

Sends formatted notifications (trade alerts, daily summaries, risk/error
messages) and exposes slash-commands for querying status, positions, PnL,
and controlling the main trading loop (pause / resume / kill).

When *bot_token* is empty every public method silently no-ops, so the
rest of the system can run without Telegram configured.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from src.copytrade.models import CopySignal
from src.executor.models import OrderResult

logger = logging.getLogger(__name__)

# Telegram hard-limits messages to 4 096 UTF-8 code-points.
_MAX_MESSAGE_LENGTH = 4096


def _truncate(text: str) -> str:
    """Truncate *text* to the Telegram message-length ceiling."""
    if len(text) <= _MAX_MESSAGE_LENGTH:
        return text
    return text[: _MAX_MESSAGE_LENGTH - 4] + " ..."


class TelegramNotifier:
    """Telegram interface for Copywallet alerts and interactive commands.

    Args:
        bot_token: Telegram Bot API token.  When empty, all methods
                   become silent no-ops so the bot can run headless.
        authorized_user_id: Telegram user-ID string that is allowed
                            to issue commands.  All other senders are
                            silently ignored.
        db: :class:`~src.state.database.Database` instance for queries.
        executor: Executor instance (paper or live) for balance / positions.
    """

    def __init__(
        self,
        bot_token: str,
        authorized_user_id: str,
        db: Any,
        executor: Any,
    ) -> None:
        self._token: str = bot_token
        self._authorized_user_id: str = authorized_user_id
        self._db = db
        self._executor = executor

        self._app: Optional[Application] = None

        # Flags inspected by the main trading loop.
        self.is_paused: bool = False
        self.is_killed: bool = False

    @property
    def _enabled(self) -> bool:
        """Return ``True`` when a bot token has been supplied."""
        return bool(self._token)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Build the Telegram application, register handlers, and begin polling.

        Polling runs in the background so this method returns immediately.
        Does nothing when no bot token is configured.
        """
        if not self._enabled:
            logger.debug("Telegram bot disabled (no token configured)")
            return

        self._app = (
            Application.builder()
            .token(self._token)
            .build()
        )

        handlers = [
            ("start", self._cmd_start),
            ("status", self._cmd_status),
            ("positions", self._cmd_positions),
            ("pnl", self._cmd_pnl),
            ("leaders", self._cmd_leaders),
            ("history", self._cmd_history),
            ("pause", self._cmd_pause),
            ("resume", self._cmd_resume),
            ("kill", self._cmd_kill),
        ]
        for name, callback in handlers:
            self._app.add_handler(CommandHandler(name, callback))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot started, polling for commands")

    async def stop(self) -> None:
        """Gracefully shut down the Telegram application."""
        if self._app is None:
            return
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bot stopped")
        except Exception:
            logger.exception("Error stopping Telegram bot")

    # ------------------------------------------------------------------
    # Alert methods  (bot --> user)
    # ------------------------------------------------------------------

    async def send_trade_alert(
        self, signal: CopySignal, result: OrderResult
    ) -> None:
        """Send a formatted trade-execution notification.

        Includes side emoji, market question, size, price, EV stats,
        and the leader being copied.
        """
        side_emoji = "\U0001f7e2" if "buy" in signal.side.lower() else "\U0001f534"
        question_short = signal.question[:50]
        text = (
            f"{side_emoji} <b>{signal.side.upper()}</b>: {question_short}\n"
            f"\U0001f4b0 ${result.size_usd:.2f} @ {result.price:.4f}\n"
            f"\U0001f4ca EV: WR {signal.leader_category_wr:.0%} "
            f"| Kelly: {signal.kelly_fraction:.1%}\n"
            f"\U0001f464 Copying: {signal.leader_username}"
        )
        await self.send_message(text)

    async def send_daily_summary(self, stats: Dict[str, Any]) -> None:
        """Send an end-of-day portfolio recap.

        Expected *stats* keys: ``count``, ``pnl``, ``pnl_pct``,
        ``bankroll``, ``open_positions``.
        """
        text = (
            "\U0001f4ca <b>Daily Summary</b>\n"
            f"Trades: {stats.get('count', 0)} "
            f"| PnL: ${stats.get('pnl', 0):+.2f} "
            f"({stats.get('pnl_pct', 0):+.1%})\n"
            f"Bankroll: ${stats.get('bankroll', 0):.2f}\n"
            f"Open positions: {stats.get('open_positions', 0)}"
        )
        await self.send_message(text)

    async def send_risk_alert(self, message: str) -> None:
        """Broadcast a risk-related warning to the operator."""
        await self.send_message(f"\u26a0\ufe0f <b>Risk Alert</b>: {message}")

    async def send_error(self, message: str) -> None:
        """Broadcast an error notification to the operator."""
        await self.send_message(f"\u274c <b>Error</b>: {message}")

    async def send_message(self, text: str) -> None:
        """Deliver a generic message to the authorised user.

        Silently catches and logs any Telegram API errors so that
        notification failures never crash the trading loop.
        """
        if not self._enabled or self._app is None:
            logger.debug("Telegram send skipped (bot not active)")
            return
        try:
            await self._app.bot.send_message(
                chat_id=self._authorized_user_id,
                text=_truncate(text),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.exception("Failed to send Telegram message")

    # ------------------------------------------------------------------
    # Command handlers  (user --> bot)
    # ------------------------------------------------------------------

    async def _check_auth(self, update: Update) -> bool:
        """Return ``True`` when the sender matches the authorised user.

        Unauthorised messages are silently discarded.
        """
        user = update.effective_user
        if user is None or str(user.id) != self._authorized_user_id:
            logger.warning(
                "Unauthorised Telegram command from user %s",
                user.id if user else "unknown",
            )
            return False
        return True

    async def _cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Respond with a welcome message listing available commands."""
        if not await self._check_auth(update):
            return
        text = (
            "\U0001f916 <b>Copywallet Bot</b>\n\n"
            "Available commands:\n"
            "/status  - Mode, balance, positions\n"
            "/positions - Open positions list\n"
            "/pnl - Profit and loss summary\n"
            "/leaders - Tracked wallets\n"
            "/history - Last 10 trades\n"
            "/pause - Pause trading\n"
            "/resume - Resume trading\n"
            "/kill - Emergency stop"
        )
        await _reply(update, text)

    async def _cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show current mode, balance, position count, and today's PnL."""
        if not await self._check_auth(update):
            return
        try:
            balance = await self._executor.get_balance()
            positions = await self._db.get_open_positions()
            today_pnl = await self._today_pnl()
            paused_tag = " (PAUSED)" if self.is_paused else ""

            text = (
                f"\U0001f4cb <b>Status</b>{paused_tag}\n"
                f"Balance: ${balance:.2f}\n"
                f"Open positions: {len(positions)}\n"
                f"Today PnL: ${today_pnl:+.2f}"
            )
            await _reply(update, text)
        except Exception:
            logger.exception("Error in /status handler")
            await _reply(update, "\u274c Error fetching status")

    async def _cmd_positions(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """List every open position with market, side, prices, and PnL."""
        if not await self._check_auth(update):
            return
        try:
            positions = await self._db.get_open_positions()
            if not positions:
                await _reply(update, "No open positions.")
                return

            lines: List[str] = ["\U0001f4c2 <b>Open Positions</b>\n"]
            for pos in positions:
                pnl = pos.get("unrealized_pnl", 0.0)
                pnl_emoji = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
                lines.append(
                    f"{pnl_emoji} {pos.get('side', '?').upper()} "
                    f"@ {pos.get('entry_price', 0):.4f} "
                    f"\u2192 {pos.get('current_price', 0):.4f} "
                    f"| ${pos.get('size_usd', 0):.2f} "
                    f"| PnL ${pnl:+.2f}"
                )

            await _reply(update, "\n".join(lines))
        except Exception:
            logger.exception("Error in /positions handler")
            await _reply(update, "\u274c Error fetching positions")

    async def _cmd_pnl(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Display today's and all-time profit/loss."""
        if not await self._check_auth(update):
            return
        try:
            today_pnl = await self._today_pnl()
            alltime = await self._alltime_pnl()

            text = (
                "\U0001f4b5 <b>Profit / Loss</b>\n"
                f"Today: ${today_pnl:+.2f}\n"
                f"All-time: ${alltime:+.2f}"
            )
            await _reply(update, text)
        except Exception:
            logger.exception("Error in /pnl handler")
            await _reply(update, "\u274c Error fetching PnL")

    async def _cmd_leaders(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """List tracked wallets with username, categories, and PnL."""
        if not await self._check_auth(update):
            return
        try:
            wallets = await self._db.get_tracked_wallets()
            if not wallets:
                await _reply(update, "No tracked wallets.")
                return

            lines: List[str] = ["\U0001f465 <b>Tracked Leaders</b>\n"]
            for w in wallets:
                username = w.get("username", "unknown")
                pnl = w.get("total_pnl", 0.0)
                cat_stats = w.get("category_stats", {})
                categories = _format_category_stats(cat_stats)
                lines.append(
                    f"\U0001f464 <b>{username}</b> | PnL ${pnl:+.2f}\n"
                    f"   {categories}"
                )

            await _reply(update, "\n".join(lines))
        except Exception:
            logger.exception("Error in /leaders handler")
            await _reply(update, "\u274c Error fetching leaders")

    async def _cmd_history(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show the last 10 trades with side, price, size, and status."""
        if not await self._check_auth(update):
            return
        try:
            trades = await self._db.get_recent_trades(limit=10)
            if not trades:
                await _reply(update, "No trade history yet.")
                return

            lines: List[str] = ["\U0001f4dc <b>Recent Trades</b>\n"]
            for t in trades:
                side = t.get("side", "?").upper()
                price = t.get("price", 0.0)
                size = t.get("size_usd", 0.0)
                status = t.get("status", "?")
                question = (t.get("question") or "")[:40]
                ts_raw = t.get("timestamp", "")
                ts_short = ts_raw[:16] if ts_raw else ""

                status_emoji = "\u2705" if status == "filled" else "\u274c"
                lines.append(
                    f"{status_emoji} {side} ${size:.2f} @ {price:.4f} "
                    f"[{status}]\n   {question} ({ts_short})"
                )

            await _reply(update, "\n".join(lines))
        except Exception:
            logger.exception("Error in /history handler")
            await _reply(update, "\u274c Error fetching history")

    async def _cmd_pause(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Pause the trading loop until /resume is issued."""
        if not await self._check_auth(update):
            return
        self.is_paused = True
        logger.info("Trading PAUSED via Telegram command")
        await _reply(update, "\u23f8\ufe0f Trading <b>paused</b>.")

    async def _cmd_resume(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Resume a previously paused trading loop."""
        if not await self._check_auth(update):
            return
        self.is_paused = False
        logger.info("Trading RESUMED via Telegram command")
        await _reply(update, "\u25b6\ufe0f Trading <b>resumed</b>.")

    async def _cmd_kill(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Set the emergency-stop flag, halting all trading permanently."""
        if not await self._check_auth(update):
            return
        self.is_killed = True
        self.is_paused = True
        logger.warning("KILL switch activated via Telegram command")
        await _reply(
            update,
            "\U0001f6d1 <b>EMERGENCY STOP</b> \u2014 bot will shut down.",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _today_pnl(self) -> float:
        """Sum ``daily_pnl`` from the most recent portfolio snapshot today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = await self._db.fetch_one(
            "SELECT daily_pnl FROM portfolio "
            "WHERE timestamp LIKE ? ORDER BY timestamp DESC LIMIT 1",
            (f"{today}%",),
        )
        return float(row["daily_pnl"]) if row else 0.0

    async def _alltime_pnl(self) -> float:
        """Return ``total_pnl`` from the newest portfolio snapshot."""
        row = await self._db.fetch_one(
            "SELECT total_pnl FROM portfolio "
            "ORDER BY timestamp DESC LIMIT 1",
            (),
        )
        return float(row["total_pnl"]) if row else 0.0


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _format_category_stats(stats: Any) -> str:
    """Format a ``category_stats`` dict into a compact one-liner.

    Each category shows its win-rate percentage.  Non-dict values are
    rendered as-is to avoid crashes on unexpected data shapes.
    """
    if not isinstance(stats, dict):
        return str(stats)

    parts: List[str] = []
    for cat, data in stats.items():
        if isinstance(data, dict):
            wr = data.get("win_rate", 0)
            parts.append(f"{cat}: {wr:.0%}")
        else:
            parts.append(f"{cat}: {data}")
    return ", ".join(parts) if parts else "no categories"


async def _reply(update: Update, text: str) -> None:
    """Reply to a Telegram update, truncating to the message limit.

    Any Telegram API error is logged and swallowed so that a single
    failed reply never takes down the bot.
    """
    if update.effective_message is None:
        return
    try:
        await update.effective_message.reply_text(
            _truncate(text),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("Failed to reply to Telegram command")
