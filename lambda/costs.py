"""Costs: the Cost and Usage Report 2.0 and the budgets of the account.

The Data Export of the account writes the report of a month under
CUR_PREFIX/data/BILLING_PERIOD=<YYYY-MM>/ as gzipped CSV, several times a
day, and ends each refresh with a manifest under CUR_PREFIX/metadata/. The
manifest wakes the function; it reads every file of the month (those the
manifest names, else all in the month's folder) and writes the sums of the
month to pk = COST, sk = <YYYY-MM>: in total, by type of line (Usage, Tax,
Credit…), by day, by service, by region, by the value of the tag COST_TAG
(the project), by day and service, and the top usage types. "usage_days"
leaves out tax, credits and refunds; the spikes are looked for in it.

Budgets are not in the report: the daily job reads them through the Budgets
API (DescribeBudgets, free) and keeps them in pk = BUDGETS, sk = SNAPSHOT.
The page reads both from the table only; the demo has budgets of its own
(BUDGETS=false, reset.py).

The daily job then looks for:
- budget_exceeded, budget_forecast: a monthly budget whose actual or
  forecasted spend is over its limit, once per budget and month;
- cost_spike: a day that cost SPIKE_FACTOR times the median of the 14 days
  before it and at least SPIKE_MIN_USD. The day looked at is the one before
  yesterday: the report of yesterday is still filling in;
- new_service: a service that costs something this month and nothing the
  month before.
"""

import csv
import gzip
import io
import json
import os
import re
import statistics
from datetime import datetime, timedelta

import common
import rules

PERIOD_RE = re.compile(r"BILLING_PERIOD=(\d{4}-\d{2})")
NOT_USAGE = {"Tax", "Credit", "Refund"}
USAGE_LIMIT = 40
# The project of a line without the tag: DynamoDB takes no empty key in a map.
UNTAGGED = "(untagged)"
SPIKE_DAYS = 14
SPIKE_MIN_BASELINE = 7


# Reports ----------------------------------------------------------------------------

def handle(bucket, key, expires=None):
    match = PERIOD_RE.search(key)
    if not match:
        return {"skipped": key}
    period = match.group(1)
    manifest = json.loads(common.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read())
    files = data_files(bucket, manifest, period)
    summary = summarize(read_rows(bucket, files), period)
    summary["files"] = len(files)
    summary["export_time"] = str(manifest.get("exportTime") or manifest.get("reportTime") or "")
    if expires:
        summary["expires"] = expires
    common.table().put_item(Item=common.clean_item(summary))
    result = {"period": period, "files": len(files), "rows": summary["rows"], "total": round(summary["total"], 2)}
    common.logger.info("report %s", json.dumps(result))
    return result


def data_files(bucket, manifest, period):
    """The keys of the month's files: as the manifest lists them, or all in
    the month's folder."""
    listed = manifest.get("dataFiles") or []
    keys = []
    for entry in listed:
        value = entry if isinstance(entry, str) else (entry or {}).get("key", "")
        if value.startswith("s3://"):
            value = value.split("/", 3)[3] if value.count("/") >= 3 else ""
        if value:
            keys.append(value)
    if keys:
        return keys
    prefix = f"{os.environ['CUR_PREFIX']}/data/BILLING_PERIOD={period}/"
    found = []
    for page in common.client("s3").get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        found += [item["Key"] for item in page.get("Contents", []) if item["Key"].endswith((".csv.gz", ".csv"))]
    return found


def read_rows(bucket, keys):
    for key in keys:
        body = common.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
        if key.endswith(".gz"):
            body = gzip.decompress(body)
        yield from csv.DictReader(io.StringIO(body.decode("utf-8-sig")))


def tag_value(raw, name):
    """The value of the tag in resource_tags, a JSON map whose keys are
    user_<tag>; the case of the key is not relied on."""
    if not raw:
        return ""
    try:
        tags = json.loads(raw)
    except ValueError:
        return ""
    wanted = ("user_" + name).lower()
    for key, value in (tags or {}).items():
        if key.lower() == wanted:
            return str(value)
    return ""


def summarize(rows, period):
    tag = os.environ.get("COST_TAG", "Project")
    total, count, currency = 0.0, 0, ""
    kinds, days, usage_days, services, regions, projects, usage = {}, {}, {}, {}, {}, {}, {}
    day_services = {}
    for row in rows:
        count += 1
        try:
            cost = float(row.get("line_item_unblended_cost") or 0)
        except ValueError:
            continue
        currency = currency or row.get("line_item_currency_code", "")
        kind = row.get("line_item_line_item_type") or "Usage"
        day = (row.get("line_item_usage_start_date") or "")[:10] or period + "-01"
        service = row.get("line_item_product_code") or "?"
        total += cost
        add(kinds, kind, cost)
        add(days, day, cost)
        add(services, service, cost)
        add(regions, row.get("product_region_code") or "global", cost)
        add(projects, tag_value(row.get("resource_tags"), tag) or UNTAGGED, cost)
        if kind not in NOT_USAGE:
            add(usage_days, day, cost)
            add(day_services.setdefault(day, {}), service, cost)
            add(usage, f"{service} · {row.get('line_item_usage_type') or '?'}", cost)
    return {
        "pk": "COST",
        "sk": period,
        "period": period,
        "currency": currency or "USD",
        "updated": common.now_iso(),
        "rows": count,
        "total": total,
        "kinds": rounded(kinds),
        "days": rounded(days),
        "usage_days": rounded(usage_days),
        "services": rounded(services),
        "regions": rounded(regions),
        "projects": rounded(projects),
        "day_services": {day: rounded(values) for day, values in day_services.items()},
        "usage": rounded(dict(sorted(usage.items(), key=lambda pair: abs(pair[1]), reverse=True)[:USAGE_LIMIT])),
    }


def add(counts, key, amount):
    counts[key] = counts.get(key, 0.0) + amount


def rounded(values):
    """Sums to 6 places; lines of zero are left out."""
    return {key: round(value, 6) for key, value in values.items() if round(value, 6) != 0}


# Budgets ----------------------------------------------------------------------------

def refresh_budgets():
    budgets = []
    paginator = common.client("budgets").get_paginator("describe_budgets")
    for page in paginator.paginate(AccountId=os.environ["ACCOUNT_ID"]):
        for budget in page.get("Budgets", []):
            spend = budget.get("CalculatedSpend") or {}
            budgets.append({
                "name": budget.get("BudgetName", ""),
                "type": budget.get("BudgetType", ""),
                "time_unit": budget.get("TimeUnit", ""),
                "limit": amount(budget.get("BudgetLimit")),
                "unit": (budget.get("BudgetLimit") or {}).get("Unit", "USD"),
                "actual": amount(spend.get("ActualSpend")),
                "forecast": amount(spend.get("ForecastedSpend")),
            })
    item = {"pk": "BUDGETS", "sk": "SNAPSHOT", "updated": common.now_iso(), "budgets": budgets}
    common.table().put_item(Item=common.clean_item(item))
    return item


def amount(spend):
    try:
        return float((spend or {}).get("Amount") or 0)
    except ValueError:
        return 0.0


def budgets_on():
    """Whether the budgets of the account are read (BUDGETS); the demo has
    its own."""
    return os.environ.get("BUDGETS", "true") == "true"


def refresh():
    """"Refresh" on the page: the latest report of this month and the month
    before, the budgets, and the checks. Without a reports bucket only the
    budgets and the checks."""
    bucket = os.environ.get("REPORTS_BUCKET", "")
    today = common.now_utc()
    read = []
    for period in (today.strftime("%Y-%m"), (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")) if bucket else ():
        manifest = latest_manifest(bucket, period)
        if manifest:
            read.append(handle(bucket, manifest))
    if budgets_on():
        refresh_budgets()
    found = check(common.day_of(today))
    return {"reports": read, "findings": len(found)}


def latest_manifest(bucket, period):
    prefix = f"{os.environ['CUR_PREFIX']}/metadata/BILLING_PERIOD={period}/"
    newest = None
    for page in common.client("s3").get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            if item["Key"].endswith("Manifest.json") and (newest is None or item["LastModified"] > newest["LastModified"]):
                newest = item
    return newest["Key"] if newest else None


# Checks -----------------------------------------------------------------------------

def check(today):
    muted = set(common.rules_settings()["muted"])
    found = []
    period = today[:7]
    month = common.get("COST", period)
    snapshot = common.get("BUDGETS", "SNAPSHOT") or {}
    for budget in snapshot.get("budgets") or []:
        if budget.get("time_unit") != "MONTHLY" or not budget.get("limit"):
            continue
        limit, actual, forecast = float(budget["limit"]), float(budget.get("actual") or 0), float(budget.get("forecast") or 0)
        details = {"budget": budget["name"], "limit": limit, "actual": actual, "forecast": forecast, "unit": budget.get("unit", "USD")}
        if actual > limit:
            found.append(finding("budget_exceeded", f"{period}#{budget['name']}", details, muted))
        elif forecast > limit:
            found.append(finding("budget_forecast", f"{period}#{budget['name']}", details, muted))
    found.append(spike(common.days_before(today, 2), muted))
    if month:
        previous = common.get("COST", (datetime.strptime(period + "-01", "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m"))
        if previous:
            before = previous.get("services") or {}
            for service, cost in (month.get("services") or {}).items():
                if float(cost) >= 0.01 and float(before.get(service, 0)) < 0.01:
                    found.append(finding("new_service", f"{period}#{service}", {"service": service, "amount": float(cost)}, muted))
    return [item for item in found if item]


def finding(rule, key, details, muted):
    if rule in muted:
        return None
    return common.save_finding(rule, rules.RULES[rule][0], "cost", common.now_iso(), key, details=details, day=details.get("day"))


def daily_usage(day):
    """The usage cost of one day and of each day before it, from the month
    items, newest first."""
    months = {}

    def month(period):
        if period not in months:
            months[period] = common.get("COST", period) or {}
        return months[period]

    values = {}
    for back in range(SPIKE_DAYS + 1):
        current = common.days_before(day, back)
        usage = month(current[:7]).get("usage_days") or {}
        if current in usage:
            values[current] = float(usage[current])
    return values, month(day[:7]).get("day_services", {}).get(day, {})


def spike(day, muted):
    values, services = daily_usage(day)
    if day not in values:
        return None
    baseline = [value for key, value in values.items() if key != day]
    if len(baseline) < SPIKE_MIN_BASELINE:
        return None
    median = statistics.median(baseline)
    cost = values[day]
    if cost < float(os.environ.get("SPIKE_MIN_USD", "0.5")) or cost <= float(os.environ.get("SPIKE_FACTOR", "2")) * median:
        return None
    topmost = sorted(((name, float(value)) for name, value in services.items()), key=lambda pair: pair[1], reverse=True)[:3]
    return finding("cost_spike", day, {"day": day, "amount": cost, "median": median, "services": dict(topmost)}, muted)
