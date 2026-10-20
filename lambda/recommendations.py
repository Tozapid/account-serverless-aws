"""Cost optimization recommendations: the Data Export of Cost Optimization Hub.

The export (stack budgets) writes the recommendations of the account once a
day under COH_PREFIX/data/ as gzipped CSV and ends with a manifest under
COH_PREFIX/metadata/, as the cost report does. The manifest wakes the
function; it reads the files the manifest names (else every file next to it
under data/) and replaces pk = RECOMMENDATIONS, sk = SNAPSHOT: the
recommendations, the biggest savings first, and what they save together.

A recommendation that saves at least SAVINGS_MIN_USD a month is a finding
(savings) the first time it is seen, once per recommendation.
"""

import csv
import gzip
import io
import json
import os

import common
import rules

LIMIT = 200
TEXT_LIMIT = 300


def handle(bucket, key):
    manifest = json.loads(common.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read())
    files = data_files(bucket, manifest, key)
    return save([recommendation(row) for row in read_rows(bucket, files)],
                str(manifest.get("exportTime") or manifest.get("reportTime") or ""))


def save(items, export_time):
    """The recommendations of an export as the snapshot, and their findings."""
    items = [item for item in items if item["id"]]
    items.sort(key=lambda item: item["savings"], reverse=True)
    snapshot = {
        "pk": "RECOMMENDATIONS", "sk": "SNAPSHOT", "updated": common.now_iso(), "export_time": export_time,
        "count": len(items), "savings": round(sum(item["savings"] for item in items), 2),
        "currency": items[0]["currency"] if items else "USD", "items": items[:LIMIT],
    }
    common.table().put_item(Item=common.clean_item(snapshot))
    found = findings(items)
    result = {"recommendations": len(items), "savings": snapshot["savings"], "findings": len(found)}
    common.logger.info("recommendations %s", json.dumps(result))
    return result


def data_files(bucket, manifest, key):
    """The keys of the export: as the manifest lists them, or every file in
    the data folder that matches the folder of the manifest."""
    keys = []
    for entry in manifest.get("dataFiles") or []:
        value = entry if isinstance(entry, str) else (entry or {}).get("key", "")
        if value.startswith("s3://"):
            value = value.split("/", 3)[3] if value.count("/") >= 3 else ""
        if value:
            keys.append(value)
    if keys:
        return keys
    prefix = key.rsplit("/", 1)[0].replace("/metadata/", "/data/", 1) + "/"
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


def number(value):
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def flag(value):
    return str(value).strip().lower() == "true"


def project(raw):
    """The value of the cost allocation tag among the tags of the resource."""
    if not raw:
        return ""
    try:
        tags = json.loads(raw)
    except ValueError:
        return ""
    if isinstance(tags, list):
        tags = {item.get("key", ""): item.get("value", "") for item in tags if isinstance(item, dict)}
    wanted = os.environ.get("COST_TAG", "Project").lower()
    return next((str(value) for name, value in (tags or {}).items() if str(name).lower() == wanted), "")


def resource_id(arn):
    """The id at the end of an ARN: i-0abc of …:instance/i-0abc, the name of a
    function of …:function:name. The export has no column of its own for it."""
    return arn.rsplit("/", 1)[-1].rsplit(":", 1)[-1] if arn else ""


def recommendation(row):
    """A row of the export as the page shows it. The savings after the
    discounts of the account when the export has them."""
    savings = row.get("estimated_monthly_savings_after_discount")
    savings = number(savings) if savings not in (None, "") else number(row.get("estimated_monthly_savings_before_discount"))
    return {
        "id": (row.get("recommendation_id") or "")[:200],
        "action": row.get("action_type") or "",
        "type": row.get("current_resource_type") or "",
        "recommended_type": row.get("recommended_resource_type") or "",
        "current": (row.get("current_resource_summary") or "")[:TEXT_LIMIT],
        "recommended": (row.get("recommended_resource_summary") or "")[:TEXT_LIMIT],
        "resource": resource_id(row.get("resource_arn") or "")[:TEXT_LIMIT],
        "arn": (row.get("resource_arn") or "")[:TEXT_LIMIT],
        "region": row.get("region") or "",
        "cost": round(number(row.get("estimated_monthly_cost_before_discount")), 4),
        "savings": round(savings, 4),
        "percent": round(number(row.get("estimated_savings_percentage_after_discount")), 1),
        "currency": row.get("currency_code") or "USD",
        "effort": row.get("implementation_effort") or "",
        "restart": flag(row.get("restart_needed")),
        "rollback": flag(row.get("rollback_possible")),
        "source": row.get("recommendation_source") or "",
        "refreshed": (row.get("last_refresh_timestamp") or "")[:19],
        "project": project(row.get("tags")),
    }


def findings(items):
    if "savings" in set(common.rules_settings()["muted"]):
        return []
    least = float(os.environ.get("SAVINGS_MIN_USD", "1"))
    found = []
    for item in items:
        if item["savings"] < least:
            continue
        details = {name: item[name] for name in ("action", "type", "resource", "savings", "currency", "current", "recommended")}
        saved = common.save_finding("savings", rules.RULES["savings"][0], "cost", common.now_iso(), item["id"],
                                    region=item["region"], details=details)
        if saved:
            found.append(saved)
    return found
