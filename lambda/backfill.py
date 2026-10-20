"""Loading a period from S3 again: the logs of CloudTrail and the reports of
the costs of any days an administrator picks, kept for as long as they say.

The live loading (trail.py, costs.py) reads only what arrives from now on,
and its events of CloudTrail live EVENT_DAYS. An administrator asks for the
days first…last and how many days to keep them (POST /admin/backfill). The
job (pk = BACKFILL, sk = <id>) runs in invocations of the function of its
own, a day of the logs or a month of the report at a time; each one goes on
while it has time and hands the rest on to the next, and the job shows how
far it got.

A day of CloudTrail is read whole and replaces what the table holds of it,
so a day read twice, or read here after the live loading read some of its
files, counts each call once:
- the log files of every region under …/CloudTrail/<region>/<YYYY/MM/DD>/
  and those of the first hour of the next day, which carry the last minutes
  of this one; only the records of the day count;
- the counters of the day (pk = TRAIL, sk = DAY#<day>) and its combinations
  (pk = COMBOS#<day>, sk = DAY) are written anew, and the items of the files
  of the live loading it now holds are deleted;
- the events worth showing, as trail.py keeps them.
A day with no logs in S3 (the bucket keeps them a year) is left as it is.
No finding is made and nothing is mailed or learned of principals and
addresses: an old event is not news.

How long: the events expire keep_days from the start of the job, but not
before the live loading would let them go (EVENT_DAYS after their time).
The counters and combinations of a day and the sums of a month expire
keep_days from the start too, unless the table already holds them for good
(the live loading writes them with no expiry); those stay for good. A month
of the report is read from its latest manifest, as "Refresh" does.

Today is left to the live loading: the last day is yesterday at most. One
job runs at a time; a job that has not moved for STALE_SECONDS (a step that
ran out of time, say) gives way to a new one.
"""

import gzip
import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import combos
import common
import costs
import rules
import trail

MAX_DAYS = 1100
KEEP_DAYS_MAX = 3650
JOB_DAYS = 365
JOBS_LISTED = 20
STALE_SECONDS = 900
# A step starts only with this much time left of the invocation.
STEP_MS = 150_000
READERS = 8
NEXT_DAY_UNTIL = "T01"
FILE_TIME_RE = re.compile(r"_(\d{8}T\d{4})Z_")


# Jobs ----------------------------------------------------------------------------------

def jobs():
    items, _ = common.query("BACKFILL", desc=True, limit=JOBS_LISTED)
    return items


def running():
    """The job that is running, if any; one that has stopped moving is marked
    stalled and does not count."""
    for job in jobs():
        if job.get("status") != "running":
            continue
        if age(job.get("updated")) < STALE_SECONDS:
            return job
        set_status(job["sk"], "stalled")
    return None


def age(moment):
    try:
        then = datetime.strptime(moment or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return (common.now_utc() - then).total_seconds()


def start(first, last, keep_days, logs, reports, me):
    now = common.now_iso()
    job_id = f"{now}#{os.urandom(3).hex()}"
    job = {
        "pk": "BACKFILL", "sk": job_id, "from": first, "to": last, "keep_days": keep_days,
        "keep_until": common.expires_in(keep_days * 86400), "trail": logs, "costs": reports,
        "by": me, "started": now, "updated": now, "status": "running",
        "next_day": first if logs else "", "next_month": first[:7] if reports else "",
        "days": 0, "files": 0, "records": 0, "events": 0, "months": 0,
        "expires": common.expires_in(JOB_DAYS * 86400),
    }
    common.save_versioned(job, 0)
    hand_on(job_id)
    return common.get("BACKFILL", job_id)


def set_status(job_id, status):
    for _ in range(trail.RETRIES):
        job = common.get("BACKFILL", job_id)
        if not job:
            return None
        if job.get("status") != "running":
            return job
        job.update(status=status, updated=common.now_iso())
        if common.save_versioned(job, int(job.get("version", 0))):
            return job
    raise RuntimeError(f"job {job_id} not written: too many writers")


def hand_on(job_id):
    common.client("lambda").invoke(FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"], InvocationType="Event",
                                   Payload=json.dumps({"action": "backfill", "job": job_id}).encode("utf-8"))


def run(job_id, context=None):
    """Steps of the job while the invocation has time; the next invocation
    goes on. A job stopped by an administrator stops after its step."""
    while True:
        job = common.get("BACKFILL", job_id)
        if not job or job.get("status") != "running":
            return {"job": job_id, "status": job.get("status") if job else None}
        version = int(job.get("version", 0))
        try:
            job["status"] = "done" if step(job) else "running"
        except Exception as exc:
            common.logger.exception("backfill %s failed", job_id)
            job.update(status="error", error=str(exc)[:300])
        job["updated"] = common.now_iso()
        if not common.save_versioned(job, version):
            # Stopped meanwhile: the loop reads it again.
            continue
        if job["status"] != "running":
            common.logger.info("backfill %s %s", job_id, job["status"])
            return {"job": job_id, "status": job["status"]}
        if context is None or context.get_remaining_time_in_millis() < STEP_MS:
            hand_on(job_id)
            return {"job": job_id, "status": "running"}


def step(job):
    """A day of the logs, then a month of the report; True when nothing is
    left."""
    until = int(job["keep_until"])
    if job.get("next_day"):
        day = job["next_day"]
        result = load_day(day, until)
        for name in ("files", "records", "events"):
            job[name] = int(job.get(name, 0)) + result[name]
        job["days"] = int(job.get("days", 0)) + 1
        following = common.days_before(day, -1)
        job["next_day"] = following if following <= job["to"] else ""
    elif job.get("next_month"):
        period = job["next_month"]
        load_month(period, until)
        job["months"] = int(job.get("months", 0)) + 1
        following = next_month(period)
        job["next_month"] = following if following <= job["to"][:7] else ""
    return not job.get("next_day") and not job.get("next_month")


def next_month(period):
    year, month = (int(part) for part in period.split("-"))
    return f"{year + month // 12:04d}-{month % 12 + 1:02d}"


def expiry(pk, sk, until):
    """None for an item the table keeps for good, else until."""
    item = common.get(pk, sk)
    return None if item and "expires" not in item else until


# CloudTrail ----------------------------------------------------------------------------

def log_files(bucket, day):
    s3 = common.client("s3")
    base = f"AWSLogs/{os.environ['ACCOUNT_ID']}/CloudTrail/"
    regions = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=base, Delimiter="/"):
        regions += [item["Prefix"] for item in page.get("CommonPrefixes", [])]
    following = common.days_before(day, -1)
    keys = []
    for region in regions:
        for folder, cut in ((day, None), (following, following.replace("-", "") + NEXT_DAY_UNTIL)):
            prefix = region + folder.replace("-", "/") + "/"
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    key = item["Key"]
                    if not key.endswith(".json.gz"):
                        continue
                    written = FILE_TIME_RE.search(key.rsplit("/", 1)[-1])
                    if cut and (not written or written.group(1) >= cut):
                        continue
                    keys.append(key)
    return keys


def read_log(bucket, key):
    body = common.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(gzip.decompress(body)).get("Records") or []


def event_expiry(record):
    moment = datetime.strptime(record["eventTime"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return int(moment.timestamp()) + int(os.environ.get("EVENT_DAYS", "90")) * 86400


def load_day(day, until):
    bucket = os.environ["TRAIL_BUCKET"]
    keys = log_files(bucket, day)
    settings = common.rules_settings()
    muted, patterns, regions = set(settings["muted"]), settings["ignored"], settings["regions"]
    counts, mix, events, files, total = trail.empty_day(), Counter(), {}, [], 0
    with ThreadPoolExecutor(READERS) as pool:
        for key, records in zip(keys, pool.map(lambda key: read_log(bucket, key), keys)):
            used = False
            for record in records:
                if (record.get("eventTime") or "")[:10] != day:
                    continue
                used = True
                total += 1
                who = rules.principal(record)
                trail.count(counts, record, who)
                mix[combos.key(record, who, trail.kind(record))] += 1
                found = not trail.ignored(who, patterns) and any(rule not in muted for rule in rules.check(record, regions))
                if found or trail.worth_keeping(record):
                    event = trail.compact(record, who)
                    event["expires"] = max(until, event_expiry(record))
                    events[event["sk"]] = event
            if used:
                files.append(combos.file_id(key))
    if total:
        expires = expiry("TRAIL", "DAY#" + day, until)
        save_counters(day, counts, expires)
        save_combinations(day, mix, files, expires)
        trail.save_events(events.values())
    result = {"day": day, "files": len(files), "records": total, "events": len(events)}
    common.logger.info("backfill %s", json.dumps(result))
    return result


def save_counters(day, counts, expires):
    item = {"pk": "TRAIL", "sk": "DAY#" + day, "day": day, "updated": common.now_iso()}
    for key in ("events", "writes", "errors", "signins"):
        item[key] = counts[key]
    for key in ("sources", "principals", "regions", "error_codes", "changes", "denied"):
        item[key] = common.top(counts[key], trail.MAP_LIMIT)
    if expires:
        item["expires"] = expires
    for _ in range(trail.RETRIES):
        current = common.get("TRAIL", "DAY#" + day) or {}
        if common.save_versioned(item, int(current.get("version", 0))):
            return
    raise RuntimeError(f"day {day} not written: too many writers")


def save_combinations(day, mix, files, expires):
    """The day's own item, as combos.compact() leaves it; the items of the
    files it holds go."""
    held = set(files)
    item = {"pk": "COMBOS#" + day, "sk": "DAY", "n": dict(combos.limit(mix)), "files": sorted(held)}
    if expires:
        item["expires"] = expires
    common.table().put_item(Item=item)
    with common.table().batch_writer() as batch:
        for old in common.query_all("COMBOS#" + day, prefix="F#"):
            if old["sk"][2:] in held:
                batch.delete_item(Key={"pk": old["pk"], "sk": old["sk"]})


# Costs ---------------------------------------------------------------------------------

def load_month(period, until):
    bucket = os.environ["REPORTS_BUCKET"]
    manifest = costs.latest_manifest(bucket, period)
    if not manifest:
        return {"period": period, "skipped": True}
    return costs.handle(bucket, manifest, expiry("COST", period, until))
