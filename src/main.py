"""Main entry point for Copywallet -- the Polymarket copy trading bot.

Wires together configuration, API client, database, wallet discovery,
copy trade monitor, paper executor, Telegram notifier, and web dashboard
into a single async run loop with graceful shutdown support.
"""

import asyncio
import logging
import signal
import sys
import threading

import uvicorn

from src.brain.claude_scorer import ClaudeScorer
from src.brain.pipeline import BrainPipeline
from src.config import load_config
from src.copytrade.discovery import WalletDiscovery
from src.copytrade.models import CopySignal
from src.copytrade.monitor import CopyTradeMonitor
from src.executor.paper import PaperExecutor
from src.monitor.dashboard import Dashboard
from src.monitor.telegram_bot import TelegramNotifier
from src.polymarket_api import PolymarketAPI
from src.rate_limiter import RateLimiter
from src.scanner.scanner import MarketScanner
from src.state.database import Database

logger = logging.getLogger("copywallet")


async def main() -> None:
    """Run the Copywallet trading bot until shutdown is requested."""

    # -- 1. Load configuration ------------------------------------------------
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = load_config(config_path)

    # -- 2. Setup logging -----------------------------------------------------
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if config.logging.file:
        handlers.append(logging.FileHandler(config.logging.file))

    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        handlers=handlers,
    )

    # -- 3. Initialize components ---------------------------------------------
    rate_limiter = RateLimiter(max_requests_per_minute=90)
    api = PolymarketAPI(rate_limiter)
    db = Database(config.database.path)
    await db.connect()

    executor = PaperExecutor(db, config.risk.starting_bankroll, config.fees)
    discovery = WalletDiscovery(api, db, config.copy_trading)
    monitor = CopyTradeMonitor(api, db, config)
    telegram = TelegramNotifier(
        config.telegram_bot_token, config.telegram_user_id, db, executor
    )
    dashboard = Dashboard(db, executor, config.dashboard)

    # -- 3b. Brain components (Phase 2) ----------------------------------------
    scanner = MarketScanner(api, db, config.scanner)
    claude_scorer = ClaudeScorer(
        config.anthropic_api_key, config.claude_brain.model
    )
    brain = BrainPipeline(claude_scorer, db, config)

    # -- 4. Graceful shutdown via OS signals -----------------------------------
    shutdown_event = asyncio.Event()

    def _handle_shutdown(*_: object) -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    # SIGINT is available everywhere; SIGTERM only on POSIX systems.
    signal.signal(signal.SIGINT, _handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_shutdown)

    # -- 5. Start Telegram bot -------------------------------------------------
    await telegram.start()

    # -- 6. Start dashboard in a daemon thread ---------------------------------
    dashboard_server = uvicorn.Server(
        uvicorn.Config(
            dashboard.app,
            host=config.dashboard.host,
            port=config.dashboard.port,
            log_level="warning",
        )
    )
    dashboard_thread = threading.Thread(
        target=dashboard_server.run, daemon=True
    )
    dashboard_thread.start()
    logger.info(
        "Dashboard running at http://%s:%d",
        config.dashboard.host,
        config.dashboard.port,
    )

    # -- 7. Initial wallet discovery -------------------------------------------
    logger.info("Running initial wallet discovery...")
    try:
        await discovery.discover()
        await monitor.refresh_wallet_list()
    except Exception as exc:
        logger.error("Initial discovery failed: %s", exc)
        await telegram.send_error(f"Initial discovery failed: {exc}")

    last_discovery = asyncio.get_event_loop().time()
    discovery_interval = config.copy_trading.discovery_interval_hours * 3600
    last_snapshot = 0.0
    snapshot_interval = 300  # portfolio snapshot every 5 minutes
    last_brain_scan = 0.0
    brain_scan_interval = (
        config.claude_brain.scan_interval_seconds
        if config.claude_brain.enabled
        else 999999
    )

    logger.info("Copywallet started in %s mode", config.mode)
    logger.info("Starting bankroll: $%.2f", config.risk.starting_bankroll)

    # -- 8. Main trading loop --------------------------------------------------
    while not shutdown_event.is_set():
        try:
            # Telegram kill switch
            if telegram.is_killed:
                logger.info("Kill command received, shutting down")
                break

            # Telegram pause flag -- sleep tight, do nothing
            if telegram.is_paused:
                await asyncio.sleep(1)
                continue

            now = asyncio.get_event_loop().time()

            # Periodic wallet re-discovery
            if now - last_discovery > discovery_interval:
                logger.info("Running periodic wallet discovery...")
                try:
                    await discovery.discover()
                    await monitor.refresh_wallet_list()
                    last_discovery = now
                except Exception as exc:
                    logger.error("Discovery failed: %s", exc)

            # Poll the next leader wallet (round-robin)
            if config.copy_trading.enabled:
                try:
                    signals = await monitor.poll_next()
                    for sig in signals:
                        result = await executor.execute(sig)
                        if result.status == "filled":
                            await telegram.send_trade_alert(sig, result)
                            logger.info(
                                "Trade executed: %s %s | $%.2f",
                                sig.side,
                                sig.question[:50],
                                result.size_usd,
                            )
                except Exception as exc:
                    logger.error("Copy trading error: %s", exc)

            # Claude Brain scanning (Phase 2)
            if (
                config.claude_brain.enabled
                and claude_scorer.is_available()
                and now - last_brain_scan > brain_scan_interval
            ):
                try:
                    markets = await scanner.scan()
                    for market in markets:
                        brain_signal = await brain.evaluate(market)
                        if brain_signal and brain_signal.action not in ("HOLD", "SELL"):
                            copy_signal = CopySignal(
                                market_id=brain_signal.market_id,
                                token_id=brain_signal.token_id,
                                question=brain_signal.question,
                                category=brain_signal.category,
                                side=brain_signal.action,
                                leader_address="",
                                leader_username="claude_brain",
                                leader_price=brain_signal.p_market,
                                current_price=brain_signal.p_market,
                                estimated_slippage=0.0,
                                position_size_usd=brain_signal.position_size_usd,
                                leader_category_wr=brain_signal.p_true,
                                kelly_fraction=brain_signal.kelly_fraction,
                                neg_risk=brain_signal.neg_risk,
                                reasoning=brain_signal.reasoning,
                            )
                            result = await executor.execute(copy_signal)
                            if result.status == "filled":
                                await telegram.send_trade_alert(
                                    copy_signal, result
                                )
                                logger.info(
                                    "Brain trade: %s %s | EV: %.3f | $%.2f",
                                    brain_signal.action,
                                    brain_signal.question[:50],
                                    brain_signal.ev,
                                    result.size_usd,
                                )
                    last_brain_scan = now
                except Exception as exc:
                    logger.error("Brain scan error: %s", exc)

            # Periodic portfolio snapshot
            if now - last_snapshot > snapshot_interval:
                try:
                    balance = await executor.get_balance()
                    positions = await executor.get_positions()
                    positions_value = sum(p.size_usd for p in positions)
                    total_value = balance + positions_value
                    total_pnl = total_value - config.risk.starting_bankroll

                    await db.snapshot_portfolio(
                        total_value, balance, positions_value, 0, total_pnl
                    )
                    last_snapshot = now

                    # Daily loss limit check
                    daily_loss_pct = (
                        -total_pnl / config.risk.starting_bankroll
                        if total_pnl < 0
                        else 0
                    )
                    if daily_loss_pct >= config.risk.daily_loss_limit_pct:
                        telegram.is_paused = True
                        await telegram.send_risk_alert(
                            f"Daily loss limit hit: {daily_loss_pct:.1%}. "
                            "Bot paused. Send /resume to restart."
                        )
                        logger.warning(
                            "Daily loss limit hit: %.1f%%",
                            daily_loss_pct * 100,
                        )
                except Exception as exc:
                    logger.error("Portfolio snapshot error: %s", exc)

        except Exception as exc:
            logger.error("Main loop error: %s", exc, exc_info=True)
            await telegram.send_error(str(exc))

        await asyncio.sleep(config.copy_trading.poll_interval_seconds)

    # -- 9. Cleanup ------------------------------------------------------------
    logger.info("Shutting down...")
    await telegram.stop()
    await api.close()
    await db.close()
    dashboard_server.should_exit = True
    logger.info("Copywallet stopped")


if __name__ == "__main__":
    asyncio.run(main())
