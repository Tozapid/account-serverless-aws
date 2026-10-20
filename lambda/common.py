"""What the parts of the function share: the table, the AWS clients, numbers
for DynamoDB and JSON, time, one-off markers and the findings.

A finding (pk = FINDINGS, sk = <time>#<rule>#<key>) is what a rule found in
CloudTrail or in the costs: the rule, its severity, when, who, where and the
details. The same rule and key make one finding only: a marker
(pk = MARK, sk = finding#<rule>#<key>) is put first, on condition that it is
not there yet.
"""

import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

FINDING_DAYS = 365
_clients = {}
_table = None


# AWS ---------------------------------------------------------------------------------

def client(name):
    if name not in _clients:
        import boto3
        _clients[name] = boto3.client(name)
    return _clients[name]


def table():
    global _table
    if _table is None:
        import boto3
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


def error_code(exc):
    data = getattr(exc, "response", None)
    return data.get("Error", {}).get("Code", "") if isinstance(data, dict) else ""


def conditional_failed(exc):
    return error_code(exc) == "ConditionalCheckFailedException"


# Time --------------------------------------------------------------------------------

def now_utc():
    return datetime.now(timezone.utc)


def iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso():
    return iso(now_utc())


def day_of(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d")


def days_before(day, count):
    return (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=count)).strftime("%Y-%m-%d")


def expires_in(seconds):
    return int(time.time()) + int(seconds)


# Numbers ------------------------------------------------------------------------------

def plain(value):
    """DynamoDB's Decimals as ints or floats, for JSON."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(item) for item in value]
    return value


def clean_item(value):
    """Floats in Decimal, as DynamoDB wants them; money to 6 places."""
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, dict):
        return {key: clean_item(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_item(item) for item in value]
    return value


def top(counts, limit):
    """The biggest entries of a map of numbers; the rest are summed into "other"."""
    if len(counts) <= limit:
        return dict(counts)
    ordered = sorted(counts.items(), key=lambda pair: pair[1], reverse=True)
    kept = dict(ordered[:limit - 1])
    kept["other"] = kept.get("other", 0) + sum(value for _, value in ordered[limit - 1:])
    return kept


# Table ----------------------------------------------------------------------------------

def get(pk, sk):
    return table().get_item(Key={"pk": pk, "sk": sk}).get("Item")


def query(pk, prefix=None, start=None, end=None, desc=False, limit=None, cursor=None):
    """One page of a partition: the items and the key to go on from, if any."""
    from boto3.dynamodb.conditions import Key

    condition = Key("pk").eq(pk)
    if prefix:
        condition = condition & Key("sk").begins_with(prefix)
    elif start is not None and end is not None:
        condition = condition & Key("sk").between(start, end)
    elif start is not None:
        condition = condition & Key("sk").gte(start)
    kwargs = {"KeyConditionExpression": condition, "ScanIndexForward": not desc}
    if limit:
        kwargs["Limit"] = limit
    if cursor:
        kwargs["ExclusiveStartKey"] = cursor
    page = table().query(**kwargs)
    return page.get("Items", []), page.get("LastEvaluatedKey")


def query_all(pk, **kwargs):
    items, cursor = [], None
    while True:
        page, cursor = query(pk, cursor=cursor, **kwargs)
        items.extend(page)
        if not cursor:
            return items


def save_versioned(item, version):
    """Writes item if nobody has written it since version was read; False
    when somebody has, and the caller reads it again."""
    try:
        table().put_item(
            Item=clean_item({**item, "version": version + 1}),
            ConditionExpression="attribute_not_exists(pk) OR version = :v",
            ExpressionAttributeValues={":v": version},
        )
        return True
    except Exception as exc:
        if conditional_failed(exc):
            return False
        raise


def mark(key, seconds):
    """A one-off marker that holds for seconds: True for the first caller."""
    now = int(time.time())
    try:
        table().put_item(
            Item={"pk": "MARK", "sk": key, "at": now_iso(), "expires": now + int(seconds)},
            ConditionExpression="attribute_not_exists(pk) OR expires < :now",
            ExpressionAttributeValues={":now": now},
        )
        return True
    except Exception as exc:
        if conditional_failed(exc):
            return False
        raise


def encode_cursor(key):
    if not key:
        return None
    return base64.urlsafe_b64encode(json.dumps(plain(key)).encode("utf-8")).decode("ascii")


def decode_cursor(value, pk):
    """A cursor of the page, only for the partition it was given for."""
    if not value:
        return None
    try:
        key = json.loads(base64.urlsafe_b64decode(value.encode("ascii")))
    except (ValueError, TypeError):
        return None
    if not isinstance(key, dict) or key.get("pk") != pk or not isinstance(key.get("sk"), str):
        return None
    return {"pk": key["pk"], "sk": key["sk"]}


# Findings ---------------------------------------------------------------------------

def save_finding(rule, severity, source, moment, key, **fields):
    """The finding, if the rule has not found this key before; None if it has."""
    if not mark(f"finding#{rule}#{key}", 400 * 86400):
        return None
    item = {
        "pk": "FINDINGS",
        "sk": f"{moment}#{rule}#{key}"[:1000],
        "rule": rule,
        "severity": severity,
        "source": source,
        "time": moment,
        "key": key[:500],
        "ack": False,
        "expires": expires_in(FINDING_DAYS * 86400),
        **{name: value for name, value in fields.items() if value not in (None, "", {}, [])},
    }
    table().put_item(Item=clean_item(item))
    return item


# Settings ---------------------------------------------------------------------------
# Administrators turn rules off, ignore principals (patterns such as
# "role/AWSReservedSSO_*") and set the regions where changes are expected.

def rules_settings():
    item = get("SETTINGS", "RULES") or {}
    regions = item.get("regions")
    if regions is None:
        regions = [region for region in os.environ.get("ALLOWED_REGIONS", "").split(",") if region]
    return {
        "muted": list(item.get("muted") or []),
        "ignored": list(item.get("ignored") or []),
        "regions": list(regions),
        "updated_at": item.get("updated_at", ""),
        "updated_by": item.get("updated_by", ""),
    }
