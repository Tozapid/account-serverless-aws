"""Traffic: the access logs of the sites and of the buckets read without
CloudFront, from the bucket LOGS_BUCKET (stack access-logs).

- cloudfront/<stack>/<yyyy>/<MM>/<dd>/…: the standard logs (v2) of the
  distribution of a site, a JSON line per request, gzipped. The source is
  cloudfront/<stack>;
- s3/<account>/<region>/<bucket>/<yyyy>/<MM>/<dd>/…: the server access logs
  of a bucket, a line per request in the format of S3. The source is
  s3/<bucket>.

Each log file counts its requests by day: in total, the bytes sent, the
answers by class and code, and the busiest paths (or keys), addresses,
countries, user agents, referrers, cache results, methods (or operations),
requesters, error codes and hours. Logs come in many small files, so each
writes a small item of its own (pk = TRAFFIC#<source>#<day>, sk = F#<file>,
for FILE_DAYS), which the daily job folds into one item of the day
(sk = DAY) that lists the files it holds, as combos.py does for CloudTrail.
A day is read from both. The lists of a file and of a day keep their
busiest entries only, so the far end of a long list is approximate.

The sources seen are pk = TRAFFIC, sk = SOURCE#<source>, with their last
day.
"""

import gzip
import hashlib
import json
import re
from collections import Counter
from datetime import datetime

import common

FILE_DAYS = 14
FILE_LIMIT = 100
DAY_LIMIT = 300
KEY_LIMIT = 200
COMPACT_DAYS = 7
MAPS = ("statuses", "paths", "ips", "countries", "agents", "referrers", "results", "methods", "requesters", "errors", "hours")
TOTALS = ("requests", "bytes", "s2xx", "s3xx", "s4xx", "s5xx", "hits", "time_ms")
SOURCE_RE = re.compile(r"(cloudfront|s3)/[\w.-]{1,100}")
TOKEN_RE = re.compile(r'\[[^\]]*\]|"[^"]*"|\S+')
CF_KEY_RE = re.compile(r"^cloudfront/([\w.-]+)/")
S3_KEY_RE = re.compile(r"^s3/\d+/[\w-]+/([\w.-]+)/")


def source_of(key):
    match = CF_KEY_RE.match(key)
    if match:
        return "cloudfront/" + match.group(1)
    match = S3_KEY_RE.match(key)
    return "s3/" + match.group(1) if match else None


def handle(bucket, key):
    source = source_of(key)
    if not source:
        return {"skipped": key}
    body = common.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    lines = body.decode("utf-8", "replace").splitlines()
    parse = cloudfront_request if source.startswith("cloudfront/") else s3_request
    days = {}
    for line in lines:
        request = parse(line)
        if request:
            count(days.setdefault(request["day"], empty()), request)
    result = save(source, days, hashlib.sha256(key.encode("utf-8")).hexdigest()[:20])
    common.logger.info("%s: %s", key, json.dumps(result))
    return result


def save(source, days, file_id):
    """The counts of a log file, by day, and the source's last day."""
    for day, counts in days.items():
        save_file(source, day, file_id, counts)
        if common.mark(f"traffic#{source}#{day}", 2 * 86400):
            common.table().put_item(Item={"pk": "TRAFFIC", "sk": "SOURCE#" + source, "source": source,
                                          "kind": source.split("/", 1)[0], "last": day, "updated": common.now_iso()})
    return {"source": source, "requests": sum(counts["requests"] for counts in days.values()), "days": sorted(days)}


# Parsing ----------------------------------------------------------------------------

def field(value):
    return "" if value in (None, "-") else str(value)


def integer(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def cloudfront_request(line):
    """A JSON line of the standard logs (v2)."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        record = json.loads(line)
    except ValueError:
        return None
    day = field(record.get("date"))
    if not day:
        return None
    try:
        taken = float(record.get("time-taken") or 0)
    except ValueError:
        taken = 0.0
    return {
        "day": day, "hour": field(record.get("time"))[:2], "ip": field(record.get("c-ip")),
        "country": field(record.get("c-country")), "method": field(record.get("cs-method")),
        "path": field(record.get("cs-uri-stem")), "status": integer(record.get("sc-status")),
        "bytes": integer(record.get("sc-bytes")), "time_ms": int(taken * 1000),
        "referrer": field(record.get("cs(Referer)")), "agent": unquote(field(record.get("cs(User-Agent)"))),
        "result": field(record.get("x-edge-result-type")),
    }


def unquote(value):
    from urllib.parse import unquote as decode
    return decode(value)


def s3_request(line):
    """A line of the server access logs of S3: owner, bucket, [time], address,
    requester, request id, operation, key, "request", status, error code,
    bytes sent, object size, total time, turnaround, "referrer", "user agent"…"""
    tokens = TOKEN_RE.findall(line)
    if len(tokens) < 17:
        return None
    try:
        moment = datetime.strptime(tokens[2].strip("[]"), "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None
    return {
        "day": common.day_of(moment), "hour": f"{moment.hour:02d}", "ip": field(tokens[3]),
        "requester": requester(field(tokens[4])), "method": field(tokens[6]), "path": field(tokens[7]),
        "status": integer(tokens[9]), "error": field(tokens[10]), "bytes": integer(tokens[11]),
        "time_ms": integer(tokens[13]), "referrer": field(tokens[15].strip('"')), "agent": field(tokens[16].strip('"')),
    }


def requester(value):
    """Who, as CloudTrail principals are named: role/<role>/<session>,
    user/<name>, a service, or anonymous."""
    if not value:
        return "anonymous"
    match = re.match(r"arn:aws:sts::\d+:assumed-role/([^/]+)/(.+)", value)
    if match:
        return f"role/{match.group(1)}/{match.group(2)}"
    match = re.match(r"arn:aws:iam::\d+:(user|root)/?(.*)", value)
    if match:
        return "root" if match.group(1) == "root" else "user/" + match.group(2)
    return value


# Counting ---------------------------------------------------------------------------

def empty():
    return {**{name: 0 for name in TOTALS}, **{name: Counter() for name in MAPS}}


def short(value):
    return value[:KEY_LIMIT] if value else ""


def count(counts, request):
    status = request["status"]
    counts["requests"] += 1
    counts["bytes"] += request["bytes"]
    counts["time_ms"] += request["time_ms"]
    group = status // 100
    if 2 <= group <= 5:
        counts[f"s{group}xx"] += 1
    if request.get("result") in ("Hit", "RefreshHit"):
        counts["hits"] += 1
    counts["statuses"][str(status or "?")] += 1
    for name, value in (("paths", request["path"]), ("ips", request["ip"]), ("countries", request.get("country")),
                        ("agents", request["agent"]), ("referrers", request["referrer"]), ("results", request.get("result")),
                        ("methods", request["method"]), ("requesters", request.get("requester")), ("errors", request.get("error")),
                        ("hours", request["hour"])):
        if value:
            counts[name][short(value)] += 1


def stored(counts, limit):
    item = {name: int(counts[name]) for name in TOTALS}
    for name in MAPS:
        item[name] = common.top(dict(counts[name]), limit) if counts[name] else {}
    return item


def save_file(source, day, file_id, counts):
    common.table().put_item(Item={"pk": f"TRAFFIC#{source}#{day}", "sk": "F#" + file_id, **stored(counts, FILE_LIMIT),
                                  "expires": common.expires_in(FILE_DAYS * 86400)})


def add(total, item):
    for name in TOTALS:
        total[name] += int(item.get(name, 0))
    for name in MAPS:
        total[name].update({key: int(value) for key, value in (item.get(name) or {}).items()})


def read(source, day):
    """The counts of a day: the folded item and the files not in it; None when
    there are none."""
    items = common.query_all(f"TRAFFIC#{source}#{day}")
    if not items:
        return None
    folded = next((item for item in items if item["sk"] == "DAY"), None)
    done = set((folded or {}).get("files") or [])
    total = empty()
    if folded:
        add(total, folded)
    for item in items:
        if item["sk"].startswith("F#") and item["sk"][2:] not in done:
            add(total, item)
    return total


def compact(source, day):
    """Folds the items of the files of a day into the item of the day."""
    items = common.query_all(f"TRAFFIC#{source}#{day}")
    files = [item for item in items if item["sk"].startswith("F#")]
    if not files:
        return 0
    folded = next((item for item in items if item["sk"] == "DAY"), None) or {}
    done = set(folded.get("files") or [])
    total = empty()
    if folded:
        add(total, folded)
    for item in files:
        if item["sk"][2:] not in done:
            add(total, item)
            done.add(item["sk"][2:])
    common.table().put_item(Item={"pk": f"TRAFFIC#{source}#{day}", "sk": "DAY", "day": day, **stored(total, DAY_LIMIT),
                                  "files": sorted(done)})
    with common.table().batch_writer() as batch:
        for item in files:
            batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    return len(files)


def sources():
    return [{"source": item["source"], "kind": item.get("kind", ""), "last": item.get("last", "")}
            for item in common.query_all("TRAFFIC", prefix="SOURCE#")]


def compact_recent(today):
    folded = 0
    for item in sources():
        for back in range(1, COMPACT_DAYS + 1):
            folded += compact(item["source"], common.days_before(today, back))
    return folded


# The page --------------------------------------------------------------------------

def totals(counts):
    return {name: int(counts[name]) for name in TOTALS}


def days(source, first, last):
    """The totals of each day of first…last; None for a day with no logs."""
    found, current = {}, last
    while current >= first:
        counts = read(source, current)
        found[current] = totals(counts) if counts else None
        current = common.days_before(current, 1)
    return found


def day(source, value):
    counts = read(source, value)
    if counts is None:
        return None
    return {**totals(counts), **{name: dict(counts[name].most_common(50)) for name in MAPS}}
