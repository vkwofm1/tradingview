"""출처·산식을 보존하는 재무 지표와 비실행형 가격 계획."""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import os

from app.us_stock_market import number


def financial_metrics(info, income, balance, cashflow, *, now):
    result = {
        "fcf": None,
        "net_debt": None,
        "roic_pct": None,
        "period_end": None,
        "period_type": "annual",
        "currency": info.get("financialCurrency"),
        "fetched_at": now.isoformat(),
        "source": "yahoo_finance_statements",
        "sector": info.get("sector"),
        "source_url": f"https://finance.yahoo.com/quote/{info.get('symbol', '')}/financials/",
        "shares_outstanding": number(info.get("sharesOutstanding")),
        "trailing_pe": number(info.get("trailingPE")),
        "forward_pe": number(info.get("forwardPE")),
        "price_to_book": number(info.get("priceToBook")),
        "inputs": {},
    }
    if info.get("currency") != "USD" or info.get("financialCurrency") != "USD":
        result["reason"] = "currency_mismatch_or_unsupported"
        return result
    if info.get("sector") in {"Financial Services", "Financial"}:
        result["reason"] = "financial_sector_requires_specialized_model"
        return result
    periods = sorted(
        set(income.columns) & set(balance.columns) & set(cashflow.columns), reverse=True
    )
    if not periods:
        result["reason"] = "aligned_annual_statements_missing"
        return result
    period = periods[0]
    result["period_end"] = period.date().isoformat()

    def value(frame, key, date=period):
        return (
            number(frame.at[key, date])
            if key in frame.index and date in frame.columns
            else None
        )

    def cash(date):
        combined = value(balance, "CashCashEquivalentsAndShortTermInvestments", date)
        return (
            combined
            if combined is not None
            else value(balance, "CashAndCashEquivalents", date)
        )

    debt, cash_now, equity = (
        value(balance, "TotalDebt"),
        cash(period),
        value(balance, "StockholdersEquity"),
    )
    ocf, capex = (
        value(cashflow, "OperatingCashFlow"),
        value(cashflow, "CapitalExpenditure"),
    )
    operating, tax, pretax = (
        value(income, "OperatingIncome"),
        value(income, "TaxProvision"),
        value(income, "PretaxIncome"),
    )
    tax_rate, tax_rate_source = None, None
    provider_tax_rate = value(income, "TaxRateForCalcs")
    if pretax is not None and pretax > 0:
        if tax is not None:
            tax_rate = tax / pretax
            tax_rate_source = "income_statement.TaxProvision/PretaxIncome"
        elif provider_tax_rate is not None:
            # 일부 제공사 응답은 세액 없이 동일 결산연도 계산용 세율만 준다.
            # 세액을 역산해 원자료로 가장하거나 전년도 세율을 섞지 않는다.
            tax_rate = provider_tax_rate
            tax_rate_source = "income_statement.TaxRateForCalcs"
    result["inputs"] = {
        "operating_cash_flow": ocf,
        "capex": capex,
        "debt": debt,
        "cash_and_short_term_investments": cash_now,
        "equity": equity,
        "operating_income": operating,
        "tax_provision": tax,
        "pretax_income": pretax,
        "effective_tax_rate": tax_rate,
        "tax_rate_source": tax_rate_source,
        "provider_tax_rate_for_calcs": provider_tax_rate,
    }
    if ocf is not None and capex is not None:
        result["fcf"] = ocf - abs(capex)
    if debt is not None and cash_now is not None:
        result["net_debt"] = debt - cash_now
    prior = next(
        (
            p
            for p in sorted(balance.columns, reverse=True)
            if 300 <= (period - p).days <= 430
        ),
        None,
    )
    if prior is not None:
        prior_debt, prior_cash, prior_equity = (
            value(balance, "TotalDebt", prior),
            cash(prior),
            value(balance, "StockholdersEquity", prior),
        )
        capital_inputs = (debt, cash_now, equity, prior_debt, prior_cash, prior_equity)
        if all(v is not None for v in capital_inputs):
            capital = (
                debt + equity - cash_now + prior_debt + prior_equity - prior_cash
            ) / 2
            result["inputs"].update(
                {
                    "average_invested_capital": capital,
                    "prior_period_end": prior.date().isoformat(),
                    "prior_debt": prior_debt,
                    "prior_cash": prior_cash,
                    "prior_equity": prior_equity,
                }
            )
            if (
                capital > 0
                and operating is not None
                and tax_rate is not None
                and 0 <= tax_rate <= 1
            ):
                result["roic_pct"] = operating * (1 - tax_rate) / capital * 100
    result["formulas"] = {
        "fcf": "operating_cash_flow - abs(capex)",
        "net_debt": "total_debt - cash_and_short_term_investments",
        "roic_pct": "100 * operating_income * (1-effective_tax_rate) / average(current,prior)(debt+equity-cash); tax_rate_source identifies reported-tax ratio or same-period provider calculation rate",
    }
    return result


def valuation(financials, price, *, price_as_of):
    shares = number(financials.get("shares_outstanding"))
    cap = price * shares if shares is not None and shares > 0 and price > 0 else None
    debt, fcf = number(financials.get("net_debt")), number(financials.get("fcf"))
    operating = number(financials.get("inputs", {}).get("operating_income"))
    ev = cap + debt if cap is not None and debt is not None else None
    return {
        "market_cap_estimate": cap,
        "enterprise_value_estimate": ev,
        "price_fcf": cap / fcf
        if cap is not None and fcf is not None and fcf > 0
        else None,
        "fcf_yield_pct": 100 * fcf / cap if cap and fcf is not None else None,
        "ev_operating_income": ev / operating
        if ev is not None and operating is not None and operating > 0
        else None,
        "trailing_pe": financials.get("trailing_pe"),
        "forward_pe": financials.get("forward_pe"),
        "price_to_book": financials.get("price_to_book"),
        "price_as_of": price_as_of,
        "shares_as_of": financials.get("fetched_at"),
        "financial_period_end": financials.get("period_end"),
        "method": "latest_price * provider_shares; EV uses dated annual net_debt; provider multiples dated fetched_at",
    }


def cost_model():
    """실비 미확인 시 예시 가정으로만 계산하고 실행 적격으로 표시하지 않는다."""
    buy = number(os.environ.get("US_STOCK_ENTRY_COST_BPS", "20"))
    sell = number(os.environ.get("US_STOCK_EXIT_COST_BPS", "20"))
    source = os.environ.get("US_STOCK_COST_SOURCE", "").strip()
    explicit = all(
        key in os.environ
        for key in ("US_STOCK_ENTRY_COST_BPS", "US_STOCK_EXIT_COST_BPS")
    )
    valid = (
        buy is not None and sell is not None and 0 <= buy < 1000 and 0 <= sell < 1000
    )
    return {
        "entry_bps": buy,
        "exit_bps": sell,
        "valid": valid,
        "confirmed": valid
        and explicit
        and bool(source)
        and os.environ.get("US_STOCK_COST_CONFIRMED") == "1",
        "source": source or f"unverified_assumption_entry_{buy}_exit_{sell}_bps",
        "scope": "all-in commission, taxes, spread, slippage and applicable FX per side",
    }


def price_plan(daily, price, costs=None):
    costs = costs if costs is not None else cost_model()
    result = {
        "entry": None,
        "target": None,
        "stop": None,
        "net_reward_risk": None,
        "min_reward_risk": 3.0,
        "costs": costs,
        "execution_authorized": False,
        "method": "20 completed daily bars support/resistance; entry limit capped by net 3R, never lift target",
    }
    if len(daily) < 20 or price is None or price <= 0 or not costs.get("valid"):
        return {**result, "status": "insufficient_data_or_invalid_costs"}
    window = daily[-20:]
    target = min(
        row["high"] for row in sorted(window, key=lambda r: r["high"], reverse=True)[:3]
    )
    support = min(row["low"] for row in window)
    # 고정 관측 범위와 평균 일중 변동폭을 사용하며 목표가를 R 배수로 만들지 않는다.
    buffer = sum(row["high"] - row["low"] for row in window[-14:]) / 14 * 0.25
    stop = support - buffer

    def floor_cent(value):
        return float(
            Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
        )

    target, stop = floor_cent(target), floor_cent(stop)
    if not 0 < stop < price or target <= stop:
        return {**result, "status": "invalid_observed_price_structure"}
    buy_factor, sell_factor = (
        1 + costs["entry_bps"] / 10000,
        1 - costs["exit_bps"] / 10000,
    )
    max_entry = (target + 3 * stop) * sell_factor / (4 * buy_factor)
    entry = floor_cent(min(price, max_entry))
    risk = entry * buy_factor - stop * sell_factor
    reward = target * sell_factor - entry * buy_factor
    if not stop < entry < target or risk <= 0 or reward <= 0:
        return {**result, "status": "net_3r_unavailable"}
    ratio = reward / risk
    return {
        **result,
        "entry": entry,
        "target": target,
        "stop": stop,
        "max_entry_for_net_3r": floor_cent(max_entry),
        "net_reward_per_share": reward,
        "net_risk_per_share": risk,
        "net_reward_risk": ratio,
        "meets_net_3r": ratio >= 3,
        "status": "waiting_for_pullback" if price > entry else "at_entry_reference",
        "cost_verified": bool(costs.get("confirmed")),
        "basis_start": window[0]["start_at"],
        "basis_end": window[-1]["end_at"],
        "price_at_calculation": price,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    }
