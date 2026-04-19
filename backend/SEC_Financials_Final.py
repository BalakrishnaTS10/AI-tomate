""" SEC EDGAR Financial Metrics Tool
=================================
Pulls 10-K (annual) and 10-Q (quarterly) financial data from SEC EDGAR XBRL API.

Usage:
    python sec_financials.py

Dependencies:
    pip install requests pandas tabulate colorama

Output views:
    1 = Annual (10-K)
    2 = Quarterly (10-Q)
    3 = Both
"""

import requests
import pandas as pd
import json
import sys
import time
from datetime import datetime, date
from tabulate import tabulate
from colorama import Fore, Style, init
from collections import defaultdict

init(autoreset=True)

HEADERS = {
    "User-Agent": "FinancialAnalysisTool research@example.com",
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov"
}

# ─────────────────────────────────────────────
# XBRL concept mappings (ordered by preference)
# ─────────────────────────────────────────────
CONCEPT_MAP = {
    "Revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RevenueFromContractWithCustomerNet",
        "TotalRevenues",
    ],
    "Cost of Goods Sold": [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
        "CostOfGoods",
    ],
    "Gross Profit": ["GrossProfit"],
    "SG&A Expense": [
        "SellingGeneralAndAdministrativeExpense",
        "GeneralAndAdministrativeExpense",
    ],
    "R&D Expense": [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ],
    "Operating Income": [
        "OperatingIncomeLoss",
        "IncomeLossFromContinuingOperationsBeforeInterestExpenseInterestIncomeIncomeTaxesExtraordinaryItemsNoncontrollingInterestsNet",
    ],
    "Interest Expense": [
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestAndDebtExpense",
    ],
    "Other Income / Expenses": [
        "NonoperatingIncomeExpense",
        "OtherNonoperatingIncomeExpense",
        "OtherIncome",
    ],
    "Pre-Tax Income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "Income Tax": [
        "IncomeTaxExpenseBenefit",
    ],
    "Net Income": [
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "ProfitLoss",
    ],
    "Depreciation & Amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
    ],
    "EPS Basic": ["EarningsPerShareBasic"],
    "EPS Diluted": ["EarningsPerShareDiluted"],
    "Basic Shares Outstanding": [
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "CommonStockSharesOutstanding",
    ],
    "Diluted Shares Outstanding": [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingDiluted",
    ],
    # Only genuine per-share XBRL tags here.
    # "DividendsCommonStockCash" is intentionally excluded — it is a total
    # dollar amount, not a per-share figure, and causes wildly wrong values.
    # A separate derivation fallback (total ÷ shares) is handled in
    # extract_dividends_per_share() below.
    "Dividends Per Share": [
        "CommonStockDividendsPerShareDeclared",
        "CommonStockDividendsPerShareCashPaid",
    ],
    # Kept separately so the fallback derivation can access the total
    "_DividendsCashTotal": [
        "DividendsCommonStockCash",
        "DividendsCommonStock",
        "PaymentsOfDividendsCommonStock",
    ],

    # ── Cash Flow Statement items (new) ──────────────────────────
    # Operating Cash Flow: total cash generated from business operations
    "Operating Cash Flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    # Capital Expenditures: cash spent on property, plant & equipment
    # NOTE: SEC reports CapEx as a negative outflow; we store the absolute value
    # and subtract it when computing FCF (see build_dataframe).
    "Capital Expenditures": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForCapitalImprovements",
        "CapitalExpenditureDiscontinuedOperations",
    ],
}

# Heuristics for scoring concepts (e.g., expected scale for revenue)
METRIC_EXPECTED_SCALE = {
    "Revenue": 1e9,
    "Cost of Goods Sold": 5e8,
    "Gross Profit": 5e8,
    "Operating Cash Flow": 5e8,
    "Capital Expenditures": 1e8,
}

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def get_cik(ticker: str) -> str:
    """Resolve ticker → zero-padded CIK."""
    www_headers = {
        "User-Agent": "FinancialAnalysisTool research@example.com",
        "Accept-Encoding": "gzip, deflate",
    }
    tickers_url = "https://www.sec.gov/files/company_tickers.json"
    r = requests.get(tickers_url, headers=www_headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    for entry in data.values():
        if entry["ticker"].upper() == ticker.upper():
            return str(entry["cik_str"]).zfill(10)
    raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR.")


def get_company_facts(cik: str) -> dict:
    """Download full XBRL company facts JSON."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def score_concept_data(data: dict, metric: str, facts: dict, cutoff_date: date) -> float:
    """Score a candidate data dict for quality: coverage, recency, magnitude."""
    if not data:
        return 0.0

    periods = sorted([datetime.strptime(k, "%Y-%m-%d").date() for k in data])
    filtered_periods = [p for p in periods if p >= cutoff_date]
    coverage = len(filtered_periods) / max(1, len(periods))

    if not filtered_periods:
        return 0.0

    latest = max(filtered_periods)
    recency = 1.0 - (date.today() - latest).days / 365.0
    recency = max(0, min(1, recency))

    total_mag = sum(abs(v) for k, v in data.items() if datetime.strptime(k, "%Y-%m-%d").date() in filtered_periods)
    expected = METRIC_EXPECTED_SCALE.get(metric, 1e6)
    mag_score = min(1.0, total_mag / expected) if expected else 0.5

    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    annual_coverage = 0
    for concept in CONCEPT_MAP.get(metric, []):
        if concept in us_gaap:
            units = us_gaap[concept].get("units", {})
            unit_data = units.get("USD") or next(iter(units.values()), None)
            if unit_data:
                annual_count = sum(1 for entry in unit_data if entry.get("form") in ("10-K", "10-K/A"))
                annual_coverage = max(annual_coverage, annual_count)
    annual_bonus = min(1.0, annual_coverage / 2) * 0.2

    return (coverage * 0.4) + (recency * 0.3) + (mag_score * 0.2) + annual_bonus


def extract_concept(facts: dict, concepts: list, form_filter: str, metric: str, cutoff_date: date) -> dict:
    """
    Extract time-series values for the best-scoring concept.
    Returns {period_end: value}
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    candidates = {}
    for concept in concepts:
        if concept not in us_gaap:
            continue
        units = us_gaap[concept].get("units", {})
        preferred_units = ["USD"] if "Expense" not in metric and "Income" not in metric else ["USD", "shares", "USD/shares"]
        unit_data = None
        for unit in preferred_units:
            if unit in units:
                unit_data = units[unit]
                break
        if unit_data is None:
            unit_data = next(iter(units.values()), None)
        if not unit_data:
            continue

        result = {}

        # Two-pass strategy for annual data:
        #   Pass 1 (strict)  330-400 days: standard calendar-year filers
        #   Pass 2 (relaxed) 300-430 days: 52/53-week fiscal-year filers
        #     e.g. Broadcom (AVGO) ends late October; some years are 364 days,
        #     others shift after acquisitions. Also rescues periods that only
        #     appear in amended 10-K/A restatements with atypical boundaries.
        passes = (
            [(330, 400)] if form_filter != "annual"
            else [(330, 400), (300, 430)]
        )

        for pass_min, pass_max in passes:
            for entry in unit_data:
                f = entry.get("form", "")
                if form_filter == "annual" and f not in ("10-K", "10-K/A"):
                    continue
                if form_filter == "quarterly" and f not in ("10-Q", "10-Q/A"):
                    continue
                if "start" not in entry and "Expense" not in metric and "Income" not in metric:
                    continue
                end = entry.get("end", "")
                val = entry.get("val")
                if val is None or end == "":
                    continue
                if "start" in entry:
                    if form_filter == "annual":
                        try:
                            start_d = datetime.strptime(entry["start"], "%Y-%m-%d")
                            end_d   = datetime.strptime(end,            "%Y-%m-%d")
                            days    = (end_d - start_d).days
                            if not (pass_min <= days <= pass_max):
                                continue
                        except Exception:
                            pass
                    if form_filter == "quarterly":
                        try:
                            start_d = datetime.strptime(entry["start"], "%Y-%m-%d")
                            end_d   = datetime.strptime(end,            "%Y-%m-%d")
                            days    = (end_d - start_d).days
                            if not (60 <= days <= 125):
                                continue
                        except Exception:
                            pass
                accn = entry.get("accn", "")
                if end not in result or accn > result[end]["accn"]:
                    result[end] = {"val": val, "accn": accn}

            # Pass 1 found data — no need for relaxed pass
            if result:
                break

        candidate_data = {k: v["val"] for k, v in result.items()}
        if candidate_data:
            candidates[concept] = candidate_data

    if not candidates:
        return {}

    scored = {c: score_concept_data(d, metric, facts, cutoff_date) for c, d in candidates.items()}
    best_concept = max(scored, key=scored.get)
    best_data = candidates[best_concept]

    if metric in ["Gross Profit"] and "Revenue" in CONCEPT_MAP and "Cost of Goods Sold" in CONCEPT_MAP:
        rev_data = extract_concept(facts, CONCEPT_MAP["Revenue"], form_filter, "Revenue", cutoff_date)
        cogs_data = extract_concept(facts, CONCEPT_MAP["Cost of Goods Sold"], form_filter, "Cost of Goods Sold", cutoff_date)
        for p in best_data:
            rev = rev_data.get(p)
            cogs = cogs_data.get(p)
            if rev is not None and cogs is not None:
                derived_gp = rev - cogs
                actual_gp = best_data[p]
                if abs(derived_gp - actual_gp) / max(abs(derived_gp), abs(actual_gp), 1e-6) > 0.05:
                    print(f"Warning: Gross Profit mismatch for {p}: derived {derived_gp/1e9:.1f}B vs actual {actual_gp/1e9:.1f}B")

    return best_data


def extract_instant_concept(facts: dict, concepts: list) -> dict:
    """Extract point-in-time values (e.g. shares outstanding, dividends)."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    for concept in concepts:
        if concept not in us_gaap:
            continue
        units = us_gaap[concept].get("units", {})
        unit_data = None
        for v in units.values():
            unit_data = v
            break
        if not unit_data:
            continue
        result = {}
        for entry in unit_data:
            end = entry.get("end", "")
            val = entry.get("val")
            if val is None or end == "":
                continue
            accn = entry.get("accn", "")
            if end not in result or accn > result[end]["accn"]:
                result[end] = {"val": val, "accn": accn}
        if result:
            return {k: v["val"] for k, v in result.items()}
    return {}


# ─────────────────────────────────────────────
# Dividends Per Share — universal safe extractor
# ─────────────────────────────────────────────

# Any "per share" value above this threshold is almost certainly a total
# dollar amount mistakenly tagged as per-share. $500 is extremely generous —
# no stock has ever paid more than a few hundred dollars per share in dividends.
_DPS_SANITY_CEILING = 500.0

def extract_dividends_per_share(facts: dict, form_filter: str, cutoff_date: date) -> dict:
    """
    Return {period_end: dps_value} using a three-tier approach:

    Tier 1 — Direct per-share XBRL tags
        CommonStockDividendsPerShareDeclared / CashPaid.
        Values are validated: anything above _DPS_SANITY_CEILING is rejected.

    Tier 2 — Derive from total cash ÷ shares outstanding
        If Tier 1 is empty or all values failed sanity, pull DividendsCommonStockCash
        (total dollars paid) and divide by WeightedAverageNumberOfSharesOutstanding.
        Result is validated with the same ceiling.

    Tier 3 — Give up gracefully; return {} so the column shows "—".
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})

    def _pull_duration(concepts: list, unit_key: str = "USD") -> dict:
        """Extract duration entries for given concepts."""
        result = {}
        for concept in concepts:
            if concept not in us_gaap:
                continue
            unit_data = us_gaap[concept].get("units", {}).get(unit_key)
            if not unit_data:
                continue
            for entry in unit_data:
                f = entry.get("form", "")
                if form_filter == "annual" and f not in ("10-K", "10-K/A"):
                    continue
                if form_filter == "quarterly" and f not in ("10-Q", "10-Q/A"):
                    continue
                if "start" not in entry:
                    continue
                try:
                    start_d = datetime.strptime(entry["start"], "%Y-%m-%d")
                    end_d   = datetime.strptime(entry["end"],   "%Y-%m-%d")
                    days    = (end_d - start_d).days
                    if form_filter == "annual"    and not (330 <= days <= 400): continue
                    if form_filter == "quarterly" and not (60  <= days <= 125): continue
                except Exception:
                    pass
                end  = entry.get("end", "")
                val  = entry.get("val")
                accn = entry.get("accn", "")
                if val is None or end == "":
                    continue
                if end not in result or accn > result[end]["accn"]:
                    result[end] = {"val": val, "accn": accn}
        return {k: v["val"] for k, v in result.items() if
                datetime.strptime(k, "%Y-%m-%d").date() >= cutoff_date}

    def _pull_shares(concepts: list) -> dict:
        """Extract weighted-average share counts (duration entries, any unit)."""
        for concept in concepts:
            if concept not in us_gaap:
                continue
            for unit_key, unit_data in us_gaap[concept].get("units", {}).items():
                result = {}
                for entry in unit_data:
                    f = entry.get("form", "")
                    if form_filter == "annual" and f not in ("10-K", "10-K/A"):
                        continue
                    if form_filter == "quarterly" and f not in ("10-Q", "10-Q/A"):
                        continue
                    if "start" not in entry:
                        continue
                    try:
                        start_d = datetime.strptime(entry["start"], "%Y-%m-%d")
                        end_d   = datetime.strptime(entry["end"],   "%Y-%m-%d")
                        days    = (end_d - start_d).days
                        if form_filter == "annual"    and not (330 <= days <= 400): continue
                        if form_filter == "quarterly" and not (60  <= days <= 125): continue
                    except Exception:
                        pass
                    end  = entry.get("end", "")
                    val  = entry.get("val")
                    accn = entry.get("accn", "")
                    if val is None or end == "" or val == 0:
                        continue
                    if end not in result or accn > result[end]["accn"]:
                        result[end] = {"val": val, "accn": accn}
                if result:
                    return {k: v["val"] for k, v in result.items()}
        return {}

    # ── Tier 1: direct per-share tags ───────────────────────────
    per_share_concepts = [
        "CommonStockDividendsPerShareDeclared",
        "CommonStockDividendsPerShareCashPaid",
    ]
    # These are stored under "USD/shares" unit in EDGAR
    tier1 = {}
    for unit_key in ("USD/shares", "USD"):
        tier1 = _pull_duration(per_share_concepts, unit_key)
        if tier1:
            break

    sane = {p: v for p, v in tier1.items() if abs(v) <= _DPS_SANITY_CEILING}
    if sane:
        return sane  # ✓ Tier 1 success

    # ── Tier 2: total cash ÷ shares ─────────────────────────────
    total_div = _pull_duration([
        "DividendsCommonStockCash",
        "DividendsCommonStock",
        "PaymentsOfDividendsCommonStock",
    ], "USD")

    share_concepts = [
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "CommonStockSharesOutstanding",
    ]
    shares = _pull_shares(share_concepts)

    if total_div and shares:
        derived = {}
        for p, total in total_div.items():
            sh = shares.get(p)
            if sh and sh > 0:
                dps = total / sh
                if abs(dps) <= _DPS_SANITY_CEILING:
                    derived[p] = dps
        if derived:
            return derived  # ✓ Tier 2 success

    # ── Tier 3: no usable data ───────────────────────────────────
    return {}


# ─────────────────────────────────────────────
# Formatting
# ─────────────────────────────────────────────

def fmt_val(val, metric_name: str) -> str:
    if val is None:
        return "—"
    name = metric_name.lower()

    if any(k in name for k in ["eps", "per share", "dividends per"]):
        return f"${val:,.2f}"

    if "shares" in name:
        m = val / 1e6
        return f"{m:,.2f}M"

    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    if abs_val >= 1e9:
        return f"{sign}${abs_val/1e9:,.2f}B"
    elif abs_val >= 1e6:
        return f"{sign}${abs_val/1e6:,.2f}M"
    elif abs_val >= 1e3:
        return f"{sign}${abs_val/1e3:,.2f}K"
    else:
        return f"{sign}${abs_val:,.2f}"


def period_label(period_end: str, freq: str, facts: dict = None) -> str:
    if facts:
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        for concept in ["NetIncomeLoss", "Revenues", "GrossProfit"]:
            if concept in us_gaap:
                for unit_data in us_gaap[concept].get("units", {}).values():
                    for entry in unit_data:
                        if entry.get("end") == period_end and "fy" in entry and "fp" in entry:
                            fy = entry["fy"]
                            fp = "Q4" if entry["fp"] == "FY" else entry["fp"]
                            if freq == "annual":
                                return f"FY{fy}"
                            else:
                                return f"FY{fy} {fp}"

    try:
        d = datetime.strptime(period_end, "%Y-%m-%d")
        if freq == "annual":
            return str(d.year)
        else:
            q = (d.month - 1) // 3 + 1
            return f"{d.year} Q{q}"
    except Exception:
        return period_end


# ─────────────────────────────────────────────
# Gap reconstruction — sum quarters → annual
# ─────────────────────────────────────────────

# Metrics where summing quarters is NOT valid (balance-sheet-style / per-share).
_NON_ADDITIVE_METRICS = {
    "EPS Basic", "EPS Diluted", "Dividends Per Share",
    "Basic Shares Outstanding", "Diluted Shares Outstanding",
    "Net Income (5YR AVG)",
}


def _get_all_quarter_entries(facts: dict, concepts: list) -> list:
    """
    Pull every XBRL duration entry that looks like a single quarter (60-125 days)
    from 10-Q, 10-Q/A, 10-K, and 10-K/A forms.
    Q4 individual-quarter rows are sometimes embedded in the annual 10-K filing
    rather than a separate 10-Q, so we must scan all form types.
    Returns a list of dicts with keys: end, start, val, accn, fy, fp, form.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    best_entries = None
    for concept in concepts:
        if concept not in us_gaap:
            continue
        units = us_gaap[concept].get("units", {})
        unit_data = units.get("USD") or next(iter(units.values()), None)
        if unit_data:
            best_entries = unit_data
            break
    if not best_entries:
        return []

    results = []
    seen = {}  # (end) -> accn, keep latest
    for entry in best_entries:
        form = entry.get("form", "")
        if form not in ("10-Q", "10-Q/A", "10-K", "10-K/A"):
            continue
        if "start" not in entry:
            continue
        end = entry.get("end", "")
        val = entry.get("val")
        if val is None or end == "":
            continue
        try:
            start_d = datetime.strptime(entry["start"], "%Y-%m-%d")
            end_d   = datetime.strptime(end,            "%Y-%m-%d")
            days    = (end_d - start_d).days
            if not (60 <= days <= 125):
                continue
        except Exception:
            continue
        accn = entry.get("accn", "")
        if end not in seen or accn > seen[end]:
            seen[end] = accn
            results.append({
                "end":   end,
                "start": entry["start"],
                "val":   val,
                "accn":  accn,
                "fy":    entry.get("fy"),   # May be None for many filers
                "fp":    entry.get("fp"),   # May be None for many filers
                "form":  form,
            })
    return results


def _infer_fiscal_year_windows(known_annual_ends: list[str]) -> list[tuple]:
    """
    Given a list of period-end dates for years we *did* successfully extract
    (e.g. ["2021-10-31", "2024-10-27", "2025-11-02"]), infer the approximate
    fiscal year start/end windows for ALL years in the span, including gaps.

    Works for any fiscal year end month — does not assume December year-end.

    Returns a list of (fy_int, window_start_date, window_end_date) tuples
    covering every year in the span of known_annual_ends.
    """
    if len(known_annual_ends) < 1:
        return []

    parsed = []
    for s in known_annual_ends:
        try:
            parsed.append(datetime.strptime(s, "%Y-%m-%d").date())
        except Exception:
            pass
    if not parsed:
        return []

    parsed.sort()

    # Compute the typical fiscal year end month and day from known periods
    # Use median month to be robust against any boundary-year outliers
    months = [d.month for d in parsed]
    days_of_month = [d.day for d in parsed]
    typical_month = sorted(months)[len(months) // 2]
    typical_day   = min(sorted(days_of_month)[len(days_of_month) // 2], 28)

    min_year = parsed[0].year
    max_year = parsed[-1].year

    windows = []
    for yr in range(min_year, max_year + 1):
        try:
            fy_end = date(yr, typical_month, typical_day)
        except ValueError:
            fy_end = date(yr, typical_month, 28)
        # Window: 340 days before fy_end to 25 days after (handles ±3 week drift)
        win_start = date(yr - 1, typical_month, typical_day) if typical_month > 1                     else date(yr - 1, 12, typical_day)
        # Simpler: just use fy_end minus 340 days as window start
        from datetime import timedelta
        win_start = fy_end - timedelta(days=360)
        win_end   = fy_end + timedelta(days=25)
        windows.append((yr, win_start, win_end))

    return windows


def reconstruct_annual_from_quarters(
    facts: dict,
    metric: str,
    concepts: list,
    cutoff_date: date,
    known_annual_ends: list[str] | None = None,
) -> dict:
    """
    Reconstruct missing annual figures by summing four individual quarters.

    Two strategies are tried in order:

    Strategy A — fy/fp metadata (fast, exact)
        Many filers populate XBRL fields ``fy`` and ``fp`` (Q1/Q2/Q3/Q4).
        If all four quarters for a fiscal year are found this way, they are summed.
        Works reliably for AAPL, MSFT, NVDA, etc.

    Strategy B — fiscal-year date-range inference (robust fallback)
        When fy/fp metadata is absent or incomplete (common after M&A restatements,
        e.g. AVGO post-VMware), the fiscal year end month is inferred from the
        period-end dates of annual periods that *were* successfully extracted.
        All quarter-length entries whose end date falls within that fiscal year's
        date window are collected. If exactly 4 non-overlapping quarters are found,
        they are summed.

    Returns {period_end_str: reconstructed_value}
    """
    from datetime import timedelta

    if metric in _NON_ADDITIVE_METRICS:
        return {}

    quarter_entries = _get_all_quarter_entries(facts, concepts)
    if not quarter_entries:
        return {}

    reconstructed: dict[str, float] = {}

    # ── Strategy A: fy/fp metadata ───────────────────────────────
    quarters_by_fy_meta: dict[int, dict[str, dict]] = defaultdict(dict)
    for e in quarter_entries:
        fy = e.get("fy")
        fp = e.get("fp")
        if fy is None or fp not in ("Q1", "Q2", "Q3", "Q4"):
            continue
        fy = int(fy)
        existing = quarters_by_fy_meta[fy].get(fp)
        if existing is None or e["accn"] > existing["accn"]:
            quarters_by_fy_meta[fy][fp] = e

    required = {"Q1", "Q2", "Q3", "Q4"}
    for fy_int, qmap in quarters_by_fy_meta.items():
        if not required.issubset(qmap.keys()):
            continue
        q4_end = qmap["Q4"]["end"]
        try:
            if datetime.strptime(q4_end, "%Y-%m-%d").date() < cutoff_date:
                continue
        except Exception:
            continue
        total = sum(qmap[q]["val"] for q in required)
        reconstructed[q4_end] = total

    if reconstructed:
        return reconstructed   # Strategy A succeeded

    # ── Strategy B: date-range inference ─────────────────────────
    if not known_annual_ends:
        return {}

    windows = _infer_fiscal_year_windows(known_annual_ends)
    if not windows:
        return {}

    # Build a lookup: end_date_str -> entry (keep latest accn per end date)
    entry_by_end: dict[str, dict] = {}
    for e in quarter_entries:
        end = e["end"]
        if end not in entry_by_end or e["accn"] > entry_by_end[end]["accn"]:
            entry_by_end[end] = e

    for fy_int, win_start, win_end in windows:
        # Skip years we already have from Strategy A or from normal extraction
        if any(
            win_start <= datetime.strptime(k, "%Y-%m-%d").date() <= win_end
            for k in reconstructed
        ):
            continue

        # Collect all quarters whose end date falls within this fiscal year window
        in_window = [
            e for e in entry_by_end.values()
            if win_start <= datetime.strptime(e["end"], "%Y-%m-%d").date() <= win_end
        ]

        if not in_window:
            continue

        # Sort by end date; verify non-overlapping coverage
        in_window.sort(key=lambda e: e["end"])

        # Validate: quarters should not overlap (each start >= previous end)
        valid = True
        for i in range(1, len(in_window)):
            try:
                prev_end = datetime.strptime(in_window[i-1]["end"], "%Y-%m-%d").date()
                curr_start = datetime.strptime(in_window[i]["start"], "%Y-%m-%d").date()
                if curr_start < prev_end:
                    valid = False
                    break
            except Exception:
                pass

        if not valid:
            continue

        # Need at least 3 quarters; 4 is ideal. With 3 we can still reconstruct
        # if they together span > 270 days of the fiscal year.
        if len(in_window) < 3:
            continue

        total_days_covered = sum(
            (datetime.strptime(e["end"], "%Y-%m-%d") -
             datetime.strptime(e["start"], "%Y-%m-%d")).days
            for e in in_window
        )

        if len(in_window) == 4:
            # All four quarters — straightforward sum
            total = sum(e["val"] for e in in_window)
            fy_period_end = in_window[-1]["end"]
            try:
                if datetime.strptime(fy_period_end, "%Y-%m-%d").date() >= cutoff_date:
                    reconstructed[fy_period_end] = total
            except Exception:
                pass

        elif len(in_window) == 3 and total_days_covered >= 270:
            # Three quarters covering 270+ days — prorate the missing quarter
            # using the average of the known three. This is an estimate and is
            # flagged with a "(est.)" note in the console but NOT in the table.
            avg_q = sum(e["val"] for e in in_window) / 3
            total = sum(e["val"] for e in in_window) + avg_q
            fy_period_end = in_window[-1]["end"]
            try:
                if datetime.strptime(fy_period_end, "%Y-%m-%d").date() >= cutoff_date:
                    reconstructed[fy_period_end] = total
                    print(Fore.YELLOW +
                          f"  ~ FY{fy_int} {metric}: only 3 quarters found, "
                          f"Q4 estimated as average of Q1–Q3 (partial data).")
            except Exception:
                pass

    return reconstructed


# ─────────────────────────────────────────────
# Core build function
# ─────────────────────────────────────────────

def build_dataframe(facts: dict, freq: str, cutoff_date: date) -> pd.DataFrame:
    """
    Build a DataFrame with metrics as rows, periods as columns.
    freq: 'annual' | 'quarterly'
    """
    form_filter = "annual" if freq == "annual" else "quarterly"
    raw: dict[str, dict] = {}

    for metric, concepts in CONCEPT_MAP.items():
        # Skip internal helper entries (prefixed with _)
        if metric.startswith("_"):
            continue

        # Dividends Per Share uses a hardened extractor with sanity checks
        # and a total-cash-÷-shares fallback; skip the generic path entirely.
        if metric == "Dividends Per Share":
            data = extract_dividends_per_share(facts, form_filter, cutoff_date)
            raw[metric] = {
                k: v for k, v in data.items()
                if datetime.strptime(k, "%Y-%m-%d").date() >= cutoff_date
            }
            continue

        data = extract_concept(facts, concepts, form_filter, metric, cutoff_date)

        # --- Logic to calculate the missing Q4 from 10-K ---
        if freq == "quarterly":
            annual_data = extract_concept(facts, concepts, "annual", metric, cutoff_date)
            for a_end, a_val in annual_data.items():
                if a_end in data:
                    continue
                a_date = datetime.strptime(a_end, "%Y-%m-%d").date()

                qs = []
                for q_end, q_val in data.items():
                    q_date = datetime.strptime(q_end, "%Y-%m-%d").date()
                    days_diff = (a_date - q_date).days
                    if 0 < days_diff <= 400:
                        qs.append((q_date, q_val))

                qs.sort(key=lambda x: x[0], reverse=True)
                prior_qs = [q[1] for q in qs[:3]]

                if len(prior_qs) > 0 and a_end not in data:
                    if any(sub in metric for sub in ["Shares Outstanding"]):
                        data[a_end] = a_val
                    else:
                        num_prior = len(prior_qs)
                        if num_prior < 3:
                            calculated = (a_val - sum(prior_qs)) / (4 - num_prior)
                        else:
                            calculated = a_val - sum(prior_qs)

                        if "Expense" in metric or "Cost" in metric:
                            calculated = max(0, calculated)
                        elif calculated < 0 and "Income" in metric:
                            calculated = max(0, calculated)

                        if abs(calculated) > 1e-6:
                            data[a_end] = calculated
        # ---------------------------------------------------

        filtered = {
            k: v for k, v in data.items()
            if datetime.strptime(k, "%Y-%m-%d").date() >= cutoff_date
        }
        raw[metric] = filtered

    # ── Gap fill: reconstruct missing annual periods from quarters ──
    # After all extraction is done, detect which fiscal years are absent
    # from the union of all period keys, then try summing Q1+Q2+Q3+Q4.
    # Only runs in annual mode; quarterly mode needs no reconstruction.
    if freq == "annual":
        all_known = {p for d in raw.values() for p in d}
        if all_known:
            # Build the expected set of period-end years in our date range
            known_years = set()
            for p in all_known:
                try:
                    known_years.add(datetime.strptime(p, "%Y-%m-%d").year)
                except Exception:
                    pass
            if known_years:
                expected_years = set(range(min(known_years), max(known_years) + 1))
                gap_years = expected_years - known_years

                if gap_years:
                    print(Fore.YELLOW +
                          f"  ↻ Attempting quarter-sum reconstruction for: "
                          f"{', '.join(f'FY{y}' for y in sorted(gap_years))}")

                    # Collect the period-end dates of years we DID extract.
                    # These anchor the fiscal-year-end month inference in
                    # Strategy B of reconstruct_annual_from_quarters().
                    known_annual_ends = []
                    revenue_data = raw.get("Revenue", {}) or raw.get("Net Income", {})
                    for p in revenue_data:
                        try:
                            yr = datetime.strptime(p, "%Y-%m-%d").year
                            if yr not in gap_years:
                                known_annual_ends.append(p)
                        except Exception:
                            pass

                    reconstructed_any = False
                    for metric, concepts in CONCEPT_MAP.items():
                        if metric.startswith("_") or metric == "Dividends Per Share":
                            continue
                        recon = reconstruct_annual_from_quarters(
                            facts, metric, concepts, cutoff_date,
                            known_annual_ends=known_annual_ends,
                        )
                        # Only fill in periods that are genuinely missing
                        for p, v in recon.items():
                            try:
                                yr = datetime.strptime(p, "%Y-%m-%d").year
                            except Exception:
                                continue
                            if yr in gap_years and p not in raw.get(metric, {}):
                                raw.setdefault(metric, {})[p] = v
                                reconstructed_any = True

                    if reconstructed_any:
                        print(Fore.GREEN +
                              "  ✓ Reconstruction successful — "
                              "gap periods filled from quarterly sums.")
                    else:
                        print(Fore.RED +
                              "  ✗ Reconstruction failed — "
                              "quarterly data insufficient for gap periods.")

    # ── Derived metrics ──────────────────────────────────────────
    all_periods = sorted({p for d in raw.values() for p in d})

    raw["EBIT"] = {}
    raw["EBITDA"] = {}
    raw["Net Income (5YR AVG)"] = {}

    # ── Free Cash Flow (new) ─────────────────────────────────────
    # Formula: FCF = Operating Cash Flow − Capital Expenditures
    #
    # SEC EDGAR stores CapEx (PaymentsToAcquirePropertyPlantAndEquipment) as a
    # POSITIVE number representing cash outflows. We subtract it from OCF to
    # arrive at FCF. If CapEx is unavailable for a period, FCF is left blank.
    raw["Free Cash Flow"] = {}
    raw["FCF Margin %"] = {}   # FCF / Revenue — useful context metric

    for p in all_periods:
        oi  = raw.get("Operating Income", {}).get(p)
        da  = raw.get("Depreciation & Amortization", {}).get(p)
        ocf = raw.get("Operating Cash Flow", {}).get(p)
        capex = raw.get("Capital Expenditures", {}).get(p)
        rev = raw.get("Revenue", {}).get(p)

        if oi is not None:
            raw["EBIT"][p] = oi
        if oi is not None and da is not None:
            raw["EBITDA"][p] = oi + da

        # FCF calculation
        if ocf is not None and capex is not None:
            # CapEx from SEC is already a positive outflow figure; subtract it
            raw["Free Cash Flow"][p] = ocf - abs(capex)

        # FCF Margin = FCF / Revenue
        if p in raw["Free Cash Flow"] and rev:
            raw["FCF Margin %"][p] = raw["Free Cash Flow"][p] / rev * 100

    # Net Income 5YR AVG (rolling 5-period average, annual only)
    if freq == "annual":
        ni_series = [(p, raw["Net Income"].get(p)) for p in all_periods if raw["Net Income"].get(p) is not None]
        ni_series.sort(key=lambda x: datetime.strptime(x[0], "%Y-%m-%d"))
        for i, (p, _) in enumerate(ni_series):
            window = [v for _, v in ni_series[max(0, i-4):i+1]]
            raw["Net Income (5YR AVG)"][p] = sum(window) / len(window)

    # ── Build DataFrame ─────────────────────────────────────────
    ordered_metrics = [
        "Revenue", "Cost of Goods Sold", "Gross Profit",
        "SG&A Expense", "R&D Expense",
        "Operating Income", "EBIT", "EBITDA", "Depreciation & Amortization",
        "Interest Expense", "Other Income / Expenses",
        "Pre-Tax Income", "Income Tax",
        "Net Income", "Net Income (5YR AVG)",
        "EPS Basic", "EPS Diluted",
        "Basic Shares Outstanding", "Diluted Shares Outstanding",
        "Dividends Per Share",
        # Cash flow items
        "Operating Cash Flow",
        "Capital Expenditures",
        "Free Cash Flow",
        "FCF Margin %",
    ]

    cols = sorted(all_periods, reverse=True)
    col_labels = [period_label(p, freq, facts) for p in cols]

    rows = []
    for metric in ordered_metrics:
        row = {"Metric": metric}
        for p, label in zip(cols, col_labels):
            val = raw.get(metric, {}).get(p)
            # FCF Margin is a percentage — format differently
            if metric == "FCF Margin %" and val is not None:
                row[label] = f"{val:,.1f}%"
            else:
                row[label] = fmt_val(val, metric)
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.set_index("Metric")
    return df


# ─────────────────────────────────────────────
# Section headers for pretty printing
# ─────────────────────────────────────────────

SECTIONS = {
    "─── INCOME STATEMENT ───": [
        "Revenue", "Cost of Goods Sold", "Gross Profit",
        "SG&A Expense", "R&D Expense",
        "Operating Income", "Interest Expense", "Other Income / Expenses",
        "Pre-Tax Income", "Income Tax",
        "Net Income", "Net Income (5YR AVG)",
        "EBITDA", "Depreciation & Amortization", "EBIT",
    ],
    "─── CASH FLOW STATEMENT ───": [
        "Operating Cash Flow",
        "Capital Expenditures",
        "Free Cash Flow",       # FCF = Operating Cash Flow − CapEx
        "FCF Margin %",         # FCF / Revenue
    ],
    "─── PER SHARE DATA ───": [
        "EPS Basic", "EPS Diluted", "Dividends Per Share",
    ],
    "─── SHARES DATA ───": [
        "Basic Shares Outstanding", "Diluted Shares Outstanding",
    ],
}


def _detect_gaps(col_labels: list, cutoff_year: int) -> list:
    """
    Return a list of fiscal-year labels that appear to be missing from col_labels.
    Works by extracting the year number from each label (e.g. "FY2022" → 2022)
    and looking for gaps in the sequence between cutoff_year and the latest year found.
    """
    years_found = set()
    for label in col_labels:
        for part in label.split():
            digits = "".join(c for c in part if c.isdigit())
            if len(digits) == 4:
                yr = int(digits)
                if cutoff_year <= yr <= date.today().year + 1:
                    years_found.add(yr)
                break

    if not years_found:
        return []

    full_range = set(range(min(years_found), max(years_found) + 1))
    missing = sorted(full_range - years_found)
    return [f"FY{y}" for y in missing]


def print_table(df: pd.DataFrame, title: str, ticker: str, company_name: str,
                cutoff_year: int = 0):
    if df.empty:
        print(Fore.RED + "  No data available.")
        return

    print()
    print(Fore.CYAN + Style.BRIGHT + "=" * 80)
    print(Fore.CYAN + Style.BRIGHT + f"  {ticker.upper()} — {company_name}")
    print(Fore.CYAN + Style.BRIGHT + f"  {title}")
    print(Fore.CYAN + Style.BRIGHT + "=" * 80)

    # ── Gap warning ──────────────────────────────────────────────
    col_labels = list(df.columns)
    missing = _detect_gaps(col_labels, cutoff_year)
    if missing:
        missing_str = ", ".join(missing)
        print()
        print(Fore.RED + Style.BRIGHT +
              f"  ⚠  Periods still missing after reconstruction: {missing_str}")
        print(Fore.RED +
              "     The quarter-sum fallback could not fill these gaps —")
        print(Fore.RED +
              "     likely because individual quarterly XBRL entries are also")
        print(Fore.RED +
              "     absent or incomplete for those fiscal years in SEC EDGAR.")
        print(Fore.RED +
              "     Tip: verify filings at → "
              f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
              f"&company={ticker}&type=10-K&dateb=&owner=include&count=40")

    for section, metrics in SECTIONS.items():
        available = [m for m in metrics if m in df.index]
        if not available:
            continue
        print()
        print(Fore.YELLOW + Style.BRIGHT + f"  {section}")
        sub = df.loc[available]
        print(tabulate(sub, headers="keys", tablefmt="rounded_outline", numalign="right"))

    out_path = f"{ticker.upper()}_{title.replace(' ','_').replace('(','').replace(')','')}.csv"
    df.to_csv(out_path)
    print()
    print(Fore.GREEN + f"  ✓ Data saved to: {out_path}")


# ─────────────────────────────────────────────
# API Backend Function
# ─────────────────────────────────────────────

def get_financial_data(ticker: str, years: int) -> dict:
    """
    Fetch and return annual financial data for a given ticker as a dictionary.
    Designed for API backend usage.
    """
    try:
        cik = get_cik(ticker)
        facts = get_company_facts(cik)
    except Exception as e:
        return {"error": str(e)}

    cutoff = date(date.today().year - years, 1, 1)
    df_annual = build_dataframe(facts, "annual", cutoff)

    if df_annual.empty:
        return {"error": f"No data available for {ticker} over the last {years} years."}

    # Convert the pandas DataFrame into a clean JSON-ready dictionary
    # Output format: {"Revenue": {"FY2023": "$383.29B", "FY2022": ...}, ...}
    return df_annual.to_dict(orient="index")