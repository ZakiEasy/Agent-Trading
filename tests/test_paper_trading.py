import pytest
from src.paper_trading_engine import execute_paper_trade, get_paper_account


@pytest.fixture
def mock_db_connection(mocker):
    # Mock get_paper_account directly to simulate sufficient funds
    mocker.patch(
        "src.paper_trading_engine.get_paper_account",
        return_value={"id": 1, "balance_eur": 10000.0, "balance_usd": 10000.0},
    )
    # Mock the DB connection to prevent actual queries
    mock_conn = mocker.MagicMock()
    mock_cursor = mocker.MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mocker.patch(
        "src.paper_trading_engine.get_db_connection",
        return_value=mocker.MagicMock(
            __enter__=mocker.MagicMock(return_value=mock_conn)
        ),
    )
    return mock_cursor


def test_execute_paper_trade_success(mock_db_connection):
    proposal = {
        "symbol": "AAPL",
        "entry_price": 150.0,
        "quantity": 10,
        "nominal_invested": 1500.0,
        "currency": "USD",
        "stop_loss_price": 140.0,
        "tp1_price": 160.0,
        "tp2_price": 170.0,
    }

    result = execute_paper_trade(proposal)
    assert result["status"] == "success"

    # Verify the SQL was executed
    assert (
        mock_db_connection.execute.call_count == 2
    )  # 1 insert position, 1 update account


def test_execute_paper_trade_insufficient_funds(mocker):
    # Mock account with 0 balance
    mocker.patch(
        "src.paper_trading_engine.get_paper_account",
        return_value={"id": 1, "balance_eur": 0.0, "balance_usd": 0.0},
    )

    proposal = {
        "symbol": "AAPL",
        "entry_price": 150.0,
        "quantity": 10,
        "nominal_invested": 1500.0,
        "currency": "USD",
    }

    result = execute_paper_trade(proposal)
    assert result["status"] == "error"
    assert "Fonds USD insuffisants" in result["message"]
