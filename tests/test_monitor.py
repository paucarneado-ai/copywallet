"""Tests for the round-robin trade monitor poller."""

from src.copytrade.monitor import RoundRobinPoller


class TestRoundRobinPoller:
    """Verify round-robin wallet cycling and update behaviour."""

    def test_cycles_through_wallets_in_order(self) -> None:
        """Poller returns wallets sequentially then wraps around."""
        poller = RoundRobinPoller(wallets=["0xA", "0xB", "0xC"])
        assert poller.next_wallet() == "0xA"
        assert poller.next_wallet() == "0xB"
        assert poller.next_wallet() == "0xC"
        assert poller.next_wallet() == "0xA"  # wraps

    def test_empty_wallets_returns_none(self) -> None:
        """Empty wallet list yields None without raising."""
        poller = RoundRobinPoller(wallets=[])
        assert poller.next_wallet() is None

    def test_update_wallets_resets_index(self) -> None:
        """Replacing the wallet list resets the cycle to the first entry."""
        poller = RoundRobinPoller(wallets=["0xA", "0xB"])
        poller.next_wallet()  # consumes 0xA
        poller.update_wallets(["0xC", "0xD", "0xE"])
        assert poller.next_wallet() == "0xC"  # index resets

    def test_single_wallet_always_returns_same(self) -> None:
        """A single-wallet list always returns that wallet."""
        poller = RoundRobinPoller(wallets=["0xOnly"])
        assert poller.next_wallet() == "0xOnly"
        assert poller.next_wallet() == "0xOnly"
        assert poller.next_wallet() == "0xOnly"

    def test_update_to_empty_returns_none(self) -> None:
        """Updating to an empty list causes next_wallet to return None."""
        poller = RoundRobinPoller(wallets=["0xA"])
        poller.update_wallets([])
        assert poller.next_wallet() is None

    def test_multiple_full_cycles(self) -> None:
        """Poller correctly cycles through multiple complete rounds."""
        poller = RoundRobinPoller(wallets=["0xA", "0xB"])
        results = [poller.next_wallet() for _ in range(6)]
        assert results == ["0xA", "0xB", "0xA", "0xB", "0xA", "0xB"]
