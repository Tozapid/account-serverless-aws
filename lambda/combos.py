"""CloudTrail by combination: the counters behind the filters of the page.

The counters of a day (trail.py, sk = DAY#<day>) keep the principals, the
services and the regions apart, so they cannot say how many calls one
principal made to one service. These do: every call of a log file is
counted under its combination of principal, service, event (for changes and
sign-ins; reads go under no event, there are too many of them), region,
error code, address and kind (r: read, w: change, s: sign-in).

- Each log file writes one small item, pk = COMBOS#<day>, sk = F#<file>:
  writing a few hundred bytes per file is far cheaper than rewriting a big
  item of the day for every file.
- The daily job folds the items of the files of past days into one,
  sk = DAY, which lists the files it holds, so that a file folded twice, or
  read both ways, counts once; then it deletes them.
- summary() counts what a filter keeps: the values clicked on the page, all
  of them at once, only changes, only errors and a text that any field may
  hold; for the chart a total per day, and for one day the tiles and the
  lists of the page. A day before the combinations existed has no data.
"""

import os
from collections import Counter

import common

SEP = "\x1f"
FIELDS = ("principal", "source", "name", "region", "error", "ip", "kind")
FILE_DAYS = 14
COMBO_LIMIT = 2500
LIST_LIMIT = 50
COMPACT_DAYS = 7


def key(record, who, kind):
    service = (record.get("eventSource") or "").replace(".amazonaws.com", "")
    return SEP.join([
        who, service, record.get("eventName", "") if kind != "r" else "", record.get("awsRegion", ""),
        record.get("errorCode", ""), record.get("sourceIPAddress", ""), kind,
    ])


def save(per_day, file_id):
    """The combinations of one log file, an item per day it touches."""
    for day, counts in per_day.items():
        if counts:
            common.table().put_item(Item={
                "pk": "COMBOS#" + day, "sk": "F#" + file_id, "n": dict(counts),
                "expires": common.expires_in(FILE_DAYS * 86400),
            })


def read(day):
    """The combinations of a day: the folded item and the files not in it;
    None when there are none."""
    items = common.query_all("COMBOS#" + day)
    folded = next((item for item in items if item["sk"] == "DAY"), None)
    if folded is None and not items:
        return None
    done = set((folded or {}).get("files") or [])
    total = Counter({name: int(value) for name, value in ((folded or {}).get("n") or {}).items()})
    for item in items:
        if item["sk"].startswith("F#") and item["sk"][2:] not in done:
            total.update({name: int(value) for name, value in (item.get("n") or {}).items()})
    return total


def compact(day):
    """Folds the items of the files of a day into its own item."""
    items = common.query_all("COMBOS#" + day)
    files = [item for item in items if item["sk"].startswith("F#")]
    if not files:
        return 0
    folded = next((item for item in items if item["sk"] == "DAY"), None) or {}
    done = set(folded.get("files") or [])
    total = Counter({name: int(value) for name, value in (folded.get("n") or {}).items()})
    for item in files:
        if item["sk"][2:] not in done:
            total.update({name: int(value) for name, value in (item.get("n") or {}).items()})
            done.add(item["sk"][2:])
    common.table().put_item(Item={"pk": "COMBOS#" + day, "sk": "DAY", "n": dict(limit(total)), "files": sorted(done)})
    with common.table().batch_writer() as batch:
        for item in files:
            batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    return len(files)


def limit(total):
    """At most COMBO_LIMIT combinations, so that the item of a day fits; the
    rarest go under one combination of their kind with no values."""
    if len(total) <= COMBO_LIMIT:
        return total
    kept = Counter(dict(total.most_common(COMBO_LIMIT - 3)))
    for name, value in total.items():
        if name not in kept:
            kept[SEP.join(["", "", "", "", "", "", name.rsplit(SEP, 1)[-1]])] += value
    return kept


def compact_recent(today):
    return sum(compact(common.days_before(today, back)) for back in range(1, COMPACT_DAYS + 1))


def matcher(fields, errors_only, writes_only, text):
    text = text.lower()

    def keep(parts):
        if errors_only and not parts["error"]:
            return False
        if writes_only and parts["kind"] != "w":
            return False
        if any(parts[name] != value for name, value in fields.items()):
            return False
        return not text or any(text in parts[name].lower() for name in FIELDS if name != "kind")
    return keep


def split(name):
    values = name.split(SEP)
    return dict(zip(FIELDS, values + [""] * (len(FIELDS) - len(values))))


def summary(first, last, day, keep):
    """Per day of first…last the calls a filter keeps (None: no data); for
    day also the tiles and the lists of the page."""
    days, detail = {}, None
    current = last
    while current >= first:
        total = read(current)
        if total is None:
            days[current] = None
        else:
            found = {"events": 0, "writes": 0, "errors": 0, "signins": 0}
            lists = {"principals": Counter(), "sources": Counter(), "changes": Counter(), "error_codes": Counter(), "regions": Counter()}
            for name, amount in total.items():
                parts = split(name)
                if not keep(parts):
                    continue
                found["events"] += amount
                found["writes"] += amount if parts["kind"] == "w" else 0
                found["signins"] += amount if parts["kind"] == "s" else 0
                found["errors"] += amount if parts["error"] else 0
                if current == day:
                    lists["principals"][parts["principal"] or "other"] += amount
                    lists["sources"][parts["source"] or "other"] += amount
                    lists["regions"][parts["region"] or "other"] += amount
                    if parts["error"]:
                        lists["error_codes"][parts["error"]] += amount
                    if parts["kind"] == "w" and not parts["error"]:
                        lists["changes"][f"{parts['source']}:{parts['name']}"] += amount
            days[current] = found
            if current == day:
                detail = {**found, **{name: dict(counts.most_common(LIST_LIMIT)) for name, counts in lists.items()}}
        current = common.days_before(current, 1)
    return {"days": days, "day": detail}


def file_id(key_name):
    import hashlib
    return hashlib.sha256(key_name.encode("utf-8")).hexdigest()[:20]


def new_file_id():
    return os.urandom(10).hex()
