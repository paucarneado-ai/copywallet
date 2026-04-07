"""Tests for the orderbook walk slippage estimator."""

from src.executor.slippage import calculate_slippage, estimate_fill_price


def _make_book(asks: list, bids: list) -> dict:
    """Build an orderbook dict with string prices/sizes (Polymarket format)."""
    return {
        "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
        "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
    }


class TestEstimateFillPrice:
    """Verify weighted average fill price from orderbook walk."""

    def test_estimate_fill_basic(self) -> None:
        """Fill entirely at first level when it has enough liquidity."""
        book = _make_book(
            asks=[(0.50, 200), (0.55, 100)],
            bids=[(0.48, 150)],
        )
        # BUY $50 at ask price 0.50 -> 100 shares at 0.50
        result = estimate_fill_price(book, "BUY", 50.0)
        assert result is not None
        assert result == 0.50

    def test_estimate_fill_walks_book(self) -> None:
        """Fill across two levels; price is between the two levels."""
        book = _make_book(
            asks=[(0.50, 60), (0.55, 200)],
            bids=[(0.48, 150)],
        )
        # BUY $50: first level has 60 shares at 0.50 -> can spend $30 (60*0.50)
        # remaining $20 at 0.55 -> 36.36 shares
        # total shares ~96.36, total cost $50
        # weighted avg = 50 / 96.36 ~= 0.5189
        result = estimate_fill_price(book, "BUY", 50.0)
        assert result is not None
        assert 0.50 < result < 0.55

    def test_estimate_fill_insufficient_liquidity(self) -> None:
        """Returns None when the book is too thin to fill the order."""
        book = _make_book(
            asks=[(0.50, 10)],  # only $5 of liquidity
            bids=[(0.48, 10)],
        )
        result = estimate_fill_price(book, "BUY", 100.0)
        assert result is None

    def test_estimate_fill_sell_uses_bids(self) -> None:
        """Sell side walks bids, not asks."""
        book = _make_book(
            asks=[(0.55, 200)],
            bids=[(0.48, 200), (0.45, 200)],
        )
        # SELL $50 walks bids: 200 shares at 0.48 -> max $96
        # $50 is within first level -> 50/0.48 ~= 104.17 shares
        result = estimate_fill_price(book, "SELL", 50.0)
        assert result is not None
        assert result == 0.48


class TestCalculateSlippage:
    """Verify slippage calculation as percentage difference."""

    def test_calculate_slippage(self) -> None:
        """Leader price 0.50, fill at 0.52 -> 4% slippage."""
        slippage = calculate_slippage(0.50, 0.52)
        assert abs(slippage - 0.04) < 1e-9
