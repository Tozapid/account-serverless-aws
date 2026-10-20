"""CloudTrail: each log file the trail writes to its bucket.

EventBridge passes on every new log file (the rule matches */CloudTrail/*,
so the digests stay out). The file is a gzipped JSON with the records of a
few minutes of one region. From it:

- the counters of each day (pk = TRAIL, sk = DAY#<day>): calls, changes,
  errors, sign-ins, and the busiest services, principals, regions, error
  codes, changes and denied calls per principal. Files of one day arrive
  side by side, so the item is read, added to and written back on condition
  that nobody wrote it in between (version), and read again if somebody did;
- the events worth showing (pk = EVENTS#<day>): changes, errors, sign-ins,
  the root user and whatever a rule found; read-only calls that worked are
  only counted. They expire after EVENT_DAYS;
- what has been seen (pk = TRAIL, sk = KNOWN): the principals that changed
  something, the addresses each principal signed in from. A principal or an
  address seen for the first time is a finding, but only after a week of
  learning, so that the first days do not flood the digest;
- the findings of the rules (rules.py); the critical ones are mailed at once
  (mail.py), at most once an hour for the same rule and principal;
- the calls by combination of principal, service, region and the rest
  (combos.py), which the filters of the page count on.

The files of CloudTrail Insights (*/CloudTrail-Insight/*) hold no calls but
an unusual rate of the calls or the errors of one API against the trail's
own baseline: the start of one is a finding (insight), its end closes it.

Administrators may turn rules off and ignore principals; an ignored
principal is still counted, but no rule looks at it.
"""

import gzip
import json
import os
from collections import Counter
from datetime import datetime, timezone

import combos
import common
import mail
import rules

MAP_LIMIT = 300
KNOWN_LIMIT = 2000
LEARN_DAYS = 7
DENIED_THRESHOLD = 20
ALERT_SECONDS = 3600
RETRIES = 10


def handle(bucket, key):
    body = common.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    records = json.loads(gzip.decompress(body)).get("Records") or []
    result = insights(records) if "/CloudTrail-Insight/" in key else process(records, combos.file_id(key))
    common.logger.info("%s: %s", key, json.dumps(result))
    return result


def process(records, file_id=None):
    settings = common.rules_settings()
    muted, patterns, regions = set(settings["muted"]), settings["ignored"], settings["regions"]
    days, events, found, mixes = {}, {}, [], {}
    writers, logins = {}, {}
    for record in records:
        if not record.get("eventTime"):
            continue
        day = record["eventTime"][:10]
        who = rules.principal(record)
        count(days.setdefault(day, empty_day()), record, who)
        mixes.setdefault(day, Counter())[combos.key(record, who, kind(record))] += 1
        hits = []
        if not ignored(who, patterns):
            hits = [rule for rule in rules.check(record, regions) if rule not in muted]
            if rules.write(record) and not record.get("errorCode") and not rules.by_aws(record):
                writers.setdefault(who, record)
            if rules.signin(record) and (record.get("responseElements") or {}).get("ConsoleLogin") == "Success":
                logins.setdefault(f"{who}|{record.get('sourceIPAddress', '')}", record)
        if hits or worth_keeping(record):
            event = compact(record, who)
            events[(event["pk"], event["sk"])] = event
        for rule in hits:
            found.append((rule, record, who))
    save_events(events.values())
    combos.save(mixes, file_id or combos.new_file_id())
    denied = {}
    for day, counts in days.items():
        denied[day] = merge_day(day, counts)
    new = []
    for rule, record, who in found:
        item = record_finding(rule, record, who)
        if item:
            new.append(item)
    new += first_seen(writers, logins, muted)
    for day, crossed in denied.items():
        for who in crossed:
            if "access_denied" in muted or ignored(who, patterns):
                continue
            item = common.save_finding("access_denied", rules.RULES["access_denied"][0], "trail", common.now_iso(),
                                       f"{day}#{who}", principal=who, day=day, details={"threshold": DENIED_THRESHOLD})
            if item:
                new.append(item)
    alert(new)
    return {"records": len(records), "events": len(events), "findings": len(new)}


def ignored(who, patterns):
    return rules.ignored(who, patterns)


def kind(record):
    """r: a read, w: a change, s: a sign-in."""
    return "s" if rules.signin(record) else "w" if rules.write(record) else "r"


def worth_keeping(record):
    return (rules.write(record) or bool(record.get("errorCode")) or rules.signin(record)
            or (record.get("userIdentity") or {}).get("type") == "Root")


# Insights --------------------------------------------------------------------------

def insights(records):
    muted = set(common.rules_settings()["muted"])
    found = []
    for record in records:
        details = record.get("insightDetails") or {}
        if details.get("state") != "Start" or "insight" in muted or not record.get("eventTime"):
            continue
        context = details.get("insightContext") or {}
        statistics = context.get("statistics") or {}
        who = next((entry for entry in context.get("attributions") or [] if entry.get("attribute") == "userIdentityArn"), {})
        top = (who.get("insight") or [{}])[0].get("value", "")
        item = common.save_finding(
            "insight", rules.RULES["insight"][0], "trail", record["eventTime"],
            record.get("sharedEventID") or record.get("eventID") or record["eventTime"],
            event_name=details.get("eventName"), event_source=(details.get("eventSource") or "").replace(".amazonaws.com", ""),
            region=record.get("awsRegion"), day=record["eventTime"][:10], error=details.get("errorCode"),
            details={name: value for name, value in {
                "type": details.get("insightType", ""), "baseline": (statistics.get("baseline") or {}).get("average"),
                "average": (statistics.get("insight") or {}).get("average"), "duration": statistics.get("insightDuration"),
                "who": top}.items() if value not in (None, "")},
        )
        if item:
            found.append(item)
    return {"insights": len(found)}


# Counters --------------------------------------------------------------------------

def empty_day():
    return {"events": 0, "writes": 0, "errors": 0, "signins": 0,
            "sources": {}, "principals": {}, "regions": {}, "error_codes": {}, "changes": {}, "denied": {}}


def add(counts, key, amount=1):
    counts[key] = counts.get(key, 0) + amount


def count(day, record, who):
    day["events"] += 1
    add(day["sources"], rules.service(record) or "?")
    add(day["principals"], who)
    add(day["regions"], record.get("awsRegion") or "?")
    if record.get("errorCode"):
        day["errors"] += 1
        add(day["error_codes"], record["errorCode"])
        if rules.denied(record):
            add(day["denied"], who)
    if rules.signin(record):
        day["signins"] += 1
    elif rules.write(record):
        day["writes"] += 1
        if not record.get("errorCode"):
            add(day["changes"], f"{rules.service(record)}:{record.get('eventName', '')}")


def merge_day(day, counts):
    """Adds the counters of one file to the day's item; returns the principals
    whose denied calls crossed DENIED_THRESHOLD with it."""
    for _ in range(RETRIES):
        item = common.get("TRAIL", "DAY#" + day) or {"pk": "TRAIL", "sk": "DAY#" + day, "day": day}
        version = int(item.get("version", 0))
        before = {key: int(value) for key, value in (item.get("denied") or {}).items()}
        merged = {"pk": "TRAIL", "sk": "DAY#" + day, "day": day, "updated": common.now_iso()}
        for key in ("events", "writes", "errors", "signins"):
            merged[key] = int(item.get(key, 0)) + counts[key]
        for key in ("sources", "principals", "regions", "error_codes", "changes", "denied"):
            total = {name: int(value) for name, value in (item.get(key) or {}).items()}
            for name, value in counts[key].items():
                add(total, name, value)
            merged[key] = common.top(total, MAP_LIMIT)
        if common.save_versioned(merged, version):
            return [who for who, value in merged["denied"].items()
                    if who != "other" and value >= DENIED_THRESHOLD > before.get(who, 0)]
    raise RuntimeError(f"day {day} not written: too many writers")


# Events --------------------------------------------------------------------------

def compact(record, who):
    """What the page shows of an event; the request is cut to 2000 characters."""
    identity = record.get("userIdentity") or {}
    event = {
        "pk": "EVENTS#" + record["eventTime"][:10],
        "sk": f"{record['eventTime']}#{record.get('eventID', '')}",
        "time": record["eventTime"],
        "id": record.get("eventID", ""),
        "name": record.get("eventName", ""),
        "source": rules.service(record),
        "principal": who,
        "identity": identity.get("type", ""),
        "arn": identity.get("arn", ""),
        "key_id": identity.get("accessKeyId", ""),
        "region": record.get("awsRegion", ""),
        "ip": record.get("sourceIPAddress", ""),
        "agent": (record.get("userAgent") or "")[:200],
        "error": record.get("errorCode", ""),
        "message": (record.get("errorMessage") or "")[:300],
        "read_only": record.get("readOnly") is True,
        "kind": record.get("eventType", ""),
        "account": record.get("recipientAccountId", ""),
        "expires": common.expires_in(int(os.environ.get("EVENT_DAYS", "90")) * 86400),
    }
    if record.get("requestParameters"):
        event["request"] = json.dumps(record["requestParameters"], ensure_ascii=False, sort_keys=True)[:2000]
    if record.get("additionalEventData"):
        event["extra"] = json.dumps(record["additionalEventData"], ensure_ascii=False, sort_keys=True)[:500]
    if rules.signin(record):
        event["result"] = (record.get("responseElements") or {}).get("ConsoleLogin", "")
    resources = [item.get("ARN") for item in record.get("resources") or [] if item.get("ARN")]
    if resources:
        event["resources"] = resources[:10]
    return {name: value for name, value in event.items() if value not in ("", None, [])}


def save_events(events):
    events = list(events)
    if not events:
        return
    with common.table().batch_writer() as batch:
        for event in events:
            batch.put_item(Item=event)


# Findings ------------------------------------------------------------------------

def record_finding(rule, record, who):
    event = compact(record, who)
    return common.save_finding(
        rule, rules.RULES[rule][0], "trail", record["eventTime"], record.get("eventID") or event["sk"],
        principal=who, region=event.get("region"), ip=event.get("ip"), event_name=event.get("name"),
        event_source=event.get("source"), error=event.get("error"), day=record["eventTime"][:10], event_sk=event["sk"],
    )


def first_seen(writers, logins, muted):
    """Principals that changed something and sign-in addresses seen for the
    first time; nothing is a finding while the stack is still learning."""
    if not writers and not logins:
        return []
    for _ in range(RETRIES):
        item = common.get("TRAIL", "KNOWN") or {}
        version = int(item.get("version", 0))
        since = item.get("since") or common.now_iso()
        known_writers = dict(item.get("principals") or {})
        known_logins = dict(item.get("logins") or {})
        new_writers = {who: record for who, record in writers.items() if who not in known_writers}
        new_logins = {key: record for key, record in logins.items() if key not in known_logins}
        if not new_writers and not new_logins:
            return []
        for who, record in new_writers.items():
            known_writers[who] = record["eventTime"]
        for key, record in new_logins.items():
            known_logins[key] = record["eventTime"]
        saved = common.save_versioned({
            "pk": "TRAIL", "sk": "KNOWN", "since": since,
            "principals": newest(known_writers), "logins": newest(known_logins),
        }, version)
        if saved:
            break
    else:
        raise RuntimeError("known principals not written: too many writers")
    if learning(since):
        return []
    found = []
    if "new_principal" not in muted:
        for who, record in new_writers.items():
            found.append(record_finding("new_principal", record, who))
    if "new_login_ip" not in muted:
        for key, record in new_logins.items():
            found.append(record_finding("new_login_ip", record, key.split("|", 1)[0]))
    return [item for item in found if item]


def newest(seen):
    """At most KNOWN_LIMIT entries, the most recently first seen."""
    if len(seen) <= KNOWN_LIMIT:
        return seen
    return dict(sorted(seen.items(), key=lambda pair: pair[1], reverse=True)[:KNOWN_LIMIT])


def learning(since):
    try:
        started = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (common.now_utc() - started).days < LEARN_DAYS


def alert(found):
    """Critical findings go out at once; the same rule and principal at most
    once an hour, the rest wait for the digest."""
    due = [item for item in found if item["severity"] == rules.CRITICAL
           and common.mark(f"alert#{item['rule']}#{item.get('principal', '')}", ALERT_SECONDS)]
    if due:
        mail.send_alerts(due)
