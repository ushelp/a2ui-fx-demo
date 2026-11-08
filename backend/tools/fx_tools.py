"""汇率相关 mock 工具 —— 数据全部为固定假数据，仅用于演示。"""
import logging

logger = logging.getLogger(__name__)

# 相对 USD 的 mock 汇率
_MOCK_RATES = {
    "USD": 1.0,
    "CNY": 7.10,
    "EUR": 0.92,
    "JPY": 149.0,
    "GBP": 0.79,
    "HKD": 7.80,
}

# mock 个人账户余额（进程内共享，重启后重置）
_MOCK_ACCOUNT = {
    "USD": 1000.0,
    "CNY": 7000.0,
    "EUR": 800.0,
    "GBP": 300.0,
    "HKD": 2000.0,
    "JPY": 120000.0,
}


def _rate(base: str, quote: str) -> float | None:
    base = base.upper()
    quote = quote.upper()
    if base not in _MOCK_RATES or quote not in _MOCK_RATES:
        return None
    return round(_MOCK_RATES[quote] / _MOCK_RATES[base], 4)


def get_exchange_rate(base_currency: str, quote_currency: str) -> dict:
    """Returns the current exchange rate between two currencies.

    Args:
        base_currency: ISO currency code to convert from, e.g. "USD".
        quote_currency: ISO currency code to convert to, e.g. "CNY".
    """
    logger.info("get_exchange_rate %s -> %s", base_currency, quote_currency)
    rate = _rate(base_currency, quote_currency)
    if rate is None:
        return {"error": f"unsupported currency pair: {base_currency}/{quote_currency}",
                "supported": sorted(_MOCK_RATES)}
    return {"base": base_currency.upper(), "quote": quote_currency.upper(), "rate": rate}


def convert_currency(amount: float, base_currency: str, quote_currency: str) -> dict:
    """Converts `amount` of base_currency into quote_currency.

    Args:
        amount: The amount of base_currency to convert.
        base_currency: ISO currency code to convert from, e.g. "USD".
        quote_currency: ISO currency code to convert to, e.g. "CNY".
    """
    logger.info("convert_currency %s %s -> %s", amount, base_currency, quote_currency)
    rate = _rate(base_currency, quote_currency)
    if rate is None:
        return {"error": f"unsupported currency pair: {base_currency}/{quote_currency}",
                "supported": sorted(_MOCK_RATES)}
    return {
        "base": base_currency.upper(),
        "quote": quote_currency.upper(),
        "rate": rate,
        "amount": amount,
        "converted": round(amount * rate, 2),
    }


def get_account_balances() -> dict:
    """Returns the mock personal account balances for all supported currencies.

    Use this whenever the user asks about their account funds or balances.
    """
    logger.info("get_account_balances")
    return {"balances": dict(_MOCK_ACCOUNT), "supported": sorted(_MOCK_ACCOUNT)}


def exchange_in_account(amount: float, base_currency: str, quote_currency: str) -> dict:
    """Executes a currency exchange against the mock personal account.

    Debits `amount` of base_currency from the account and credits the converted
    amount of quote_currency. Use this only when the user confirms an exchange
    (e.g. submits the "convert" action); for a read-only estimate use
    convert_currency instead.

    Args:
        amount: The amount of base_currency to exchange.
        base_currency: ISO currency code to debit, e.g. "USD".
        quote_currency: ISO currency code to credit, e.g. "CNY".
    """
    logger.info("exchange_in_account %s %s -> %s", amount, base_currency, quote_currency)
    base = base_currency.upper()
    quote = quote_currency.upper()

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"error": "兑换金额必须是数字", "balances": dict(_MOCK_ACCOUNT)}

    rate = _rate(base, quote)
    if rate is None:
        return {"error": f"unsupported currency pair: {base}/{quote}",
                "supported": sorted(_MOCK_RATES)}
    if amount <= 0:
        return {"error": "兑换金额必须大于 0", "balances": dict(_MOCK_ACCOUNT)}

    available = _MOCK_ACCOUNT.get(base, 0.0)
    if available < amount:
        return {
            "error": f"{base} 余额不足：当前 {available:.2f}，本次需要 {amount:.2f}",
            "balances": dict(_MOCK_ACCOUNT),
        }

    converted = round(amount * rate, 2)
    _MOCK_ACCOUNT[base] = round(available - amount, 2)
    _MOCK_ACCOUNT[quote] = round(_MOCK_ACCOUNT.get(quote, 0.0) + converted, 2)

    logger.info("account after exchange: %s", _MOCK_ACCOUNT)
    return {
        "base": base,
        "quote": quote,
        "rate": rate,
        "amount": round(amount, 2),
        "converted": converted,
        "balances": dict(_MOCK_ACCOUNT),
    }
