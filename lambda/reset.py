"""Reset of the public demo: a made-up account, always recent.

The page reads the table only, so the demo fills it with what the reports
and the logs of an account would leave there, made from seed/seed.json by
the code that reads a real account:

- costs: lines of a Cost and Usage Report, day by day, for the months of
  the seed up to today, summed by costs.summarize() as a report would be,
  with a spike two days ago and a service new this month; the budgets of the
  seed against them; then the checks of the daily job (costs.check());
- CloudTrail: the records of the days of the seed, the routine of each
  principal and the incidents, through trail.process() as log files would
  go, so the counters, the events, the combinations and the findings are
  the real ones. The principals are learned first; the incidents of the last
  days are news;
- the findings the seed calls acknowledged are acknowledged by the demo
  user.

A day of the demo is made once (pk = SEED, sk = STATE): the first reset of a
UTC day, or one after a deploy, wipes the table and makes it all again,
dated back from today. Every other reset is light, so that the demo costs
cents: it adds the records of the time passed since the last one, puts back
what a visitor may change (the rules, the mails a user wants, the
acknowledged findings) and the users: the demo user gets the published
password back, two-factor authentication off, the default language, the
administrator role, and is signed out everywhere; any other user goes.
"""

import hashlib
import json
import logging
import os
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import combos
import common
import costs
import trail

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Next to the code in the function's zip; in the repository, in seed/.
SEED = next(path for path in (Path(__file__).with_name("seed.json"), Path(__file__).parent.parent / "seed" / "seed.json")
            if path.exists())
LEARNED_BEFORE = 30


def handler(event, context):
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    now = common.now_utc().replace(microsecond=0)
    owner = os.environ["DEMO_EMAIL"].lower()
    state = common.get("SEED", "STATE") or {}
    full = (event or {}).get("reason") == "deploy" or state.get("day") != now.date().isoformat()
    if full:
        result = {"full": True, "removed": wipe_table()}
        result.update(write_costs(seed, now))
        result["records"] = write_trail(seed, now)
    else:
        result = {"full": False, "records": add_records(seed, datetime.fromisoformat(state["until"].replace("Z", "+00:00")), now)}
    common.table().put_item(Item={"pk": "SEED", "sk": "STATE", "day": now.date().isoformat(), "until": common.iso(now)})
    result["restored"] = restore(seed, owner)
    reset_users(owner)
    logger.info("reset: %s", json.dumps(result))
    return result


def wipe_table():
    keys = []
    kwargs = {"ProjectionExpression": "pk, sk"}
    while True:
        page = common.table().scan(**kwargs)
        keys.extend({"pk": item["pk"], "sk": item["sk"]} for item in page.get("Items", []))
        if not page.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    with common.table().batch_writer() as batch:
        for key in keys:
            batch.delete_item(Key=key)
    return len(keys)


# Costs ---------------------------------------------------------------------------------

def months_back(day, count):
    year, month = day.year, day.month - count
    while month < 1:
        year, month = year - 1, month + 12
    return date(year, month, 1)


def month_days(day):
    following = months_back(day, -1)
    return (following - date(day.year, day.month, 1)).days


def cost_rows(spec, now):
    """The lines of the report by month: a line per day and service, the
    spike, and tax and credits on the months that are closed."""
    today = now.date()
    jitter = float(spec.get("jitter", 0))
    tag = os.environ.get("COST_TAG", "Project")
    spike = spec.get("spike")
    spike_day = today - timedelta(days=spike["days_ago"]) if spike else None
    first = months_back(today, int(spec.get("months", 3)) - 1)
    rows, usage = {}, {}

    def line(day, entry, amount, kind="Usage"):
        period = day.strftime("%Y-%m")
        rows.setdefault(period, []).append({
            "line_item_unblended_cost": f"{amount:.6f}", "line_item_currency_code": "USD",
            "line_item_line_item_type": kind, "line_item_usage_start_date": day.isoformat() + "T00:00:00Z",
            "line_item_product_code": entry["code"],
            "product_region_code": "" if entry.get("region") == "global" else entry.get("region", ""),
            "resource_tags": json.dumps({"user_" + tag: entry["project"]}) if entry.get("project") else "",
            "line_item_usage_type": entry.get("usage", ""),
        })
        if kind == "Usage":
            usage.setdefault(period, {}).setdefault(entry["code"], [entry, 0.0])[1] += amount

    day = first
    while day <= today:
        rng = random.Random(f"cost:{day}")
        # The report of today is a few hours in.
        share = (now.hour * 60 + now.minute) / 1440 if day == today else 1.0
        for entry in spec["services"]:
            if entry.get("this_month") and (day.year, day.month) != (today.year, today.month):
                continue
            if "monthly" in entry:
                amount = float(entry["monthly"]) if day.day == 1 else 0.0
            else:
                amount = float(entry["daily"]) * (1 + rng.uniform(-jitter, jitter))
                if entry.get("weekdays") and day.weekday() >= 5:
                    amount *= 0.1
                amount *= share
            if amount > 0:
                line(day, entry, amount)
        if day == spike_day:
            line(day, spike, float(spike["amount"]))
        day += timedelta(days=1)
    current = today.strftime("%Y-%m")
    for period, services in usage.items():
        if period == current:
            continue
        month_start = date.fromisoformat(period + "-01")
        for entry, amount in services.values():
            line(month_start, entry, amount * float(spec.get("tax_rate", 0)), "Tax")
    for credit in spec.get("credits", []):
        month_start = months_back(today, credit["months_ago"])
        if month_start >= first:
            line(month_start, {"code": "AmazonEC2", "region": "global"}, float(credit["amount"]), "Credit")
    return rows


def write_costs(seed, now):
    spec = seed["costs"]
    months = {}
    for period, rows in sorted(cost_rows(spec, now).items()):
        summary = costs.summarize(rows, period)
        summary.update(files=1, export_time=common.iso(now))
        common.table().put_item(Item=common.clean_item(summary))
        months[period] = summary
    current = months.get(now.strftime("%Y-%m")) or {}
    elapsed = now.day - 1 + (now.hour * 60 + now.minute) / 1440
    budgets = []
    for budget in spec.get("budgets", []):
        actual = float((current.get("services") or {}).get(budget["service"], 0)) if budget.get("service") else float(current.get("total", 0))
        forecast = actual / elapsed * month_days(now.date()) if elapsed > 0 else actual
        budgets.append({"name": budget["name"], "type": "COST", "time_unit": "MONTHLY", "limit": float(budget["limit"]),
                        "unit": "USD", "actual": round(actual, 2), "forecast": round(forecast, 2)})
    common.table().put_item(Item=common.clean_item({"pk": "BUDGETS", "sk": "SNAPSHOT", "updated": common.iso(now), "budgets": budgets}))
    found = costs.check(now.date().isoformat())
    return {"months": len(months), "cost_findings": len(found)}


# CloudTrail ----------------------------------------------------------------------------

def identity_of(spec, account, rng):
    kind = spec["type"]
    if kind == "Root":
        return {"type": "Root", "principalId": account, "arn": f"arn:aws:iam::{account}:root", "accountId": account,
                "accessKeyId": "ASIA" + key_part(rng)}
    if kind == "IAMUser":
        return {"type": "IAMUser", "principalId": "AIDA" + key_part(rng), "arn": f"arn:aws:iam::{account}:user/{spec['user']}",
                "accountId": account, "accessKeyId": "AKIA" + key_part(rng), "userName": spec["user"]}
    if kind == "AWSService":
        return {"type": "AWSService", "invokedBy": spec["service"]}
    role, session = spec["role"], spec["session"]
    return {"type": "AssumedRole", "principalId": "AROA" + key_part(rng) + ":" + session,
            "arn": f"arn:aws:sts::{account}:assumed-role/{role}/{session}", "accountId": account,
            "accessKeyId": "ASIA" + key_part(rng),
            "sessionContext": {"sessionIssuer": {"type": "Role", "principalId": "AROA" + key_part(rng),
                                                 "arn": f"arn:aws:iam::{account}:role/{role}", "accountId": account, "userName": role},
                               "attributes": {"mfaAuthenticated": "false"}}}


def key_part(rng):
    return "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567") for _ in range(16))


def record(principal, account, call, name, moment, rng):
    who = principal["identity"]
    by_service = who["type"] == "AWSService"
    source = call["source"]
    item = {
        "eventVersion": "1.11",
        "userIdentity": identity_of(who, account, rng),
        "eventTime": common.iso(moment),
        "eventSource": f"{source}.amazonaws.com",
        "eventName": name,
        "awsRegion": call.get("region", "eu-central-1"),
        "sourceIPAddress": who.get("service") if by_service else principal.get("ip", ""),
        "userAgent": who.get("service") if by_service else principal.get("agent", ""),
        "requestParameters": call.get("params"),
        "eventID": str(hashlib.md5(f"{moment.isoformat()}#{name}#{rng.random()}".encode()).hexdigest()),
        "readOnly": not call.get("write", False),
        "eventType": call.get("event_type", "AwsApiCall"),
        "managementEvent": True,
        "recipientAccountId": account,
    }
    if name == "ConsoleLogin":
        item.update(eventType="AwsConsoleSignIn", readOnly=False, responseElements={"ConsoleLogin": call.get("result", "Success")},
                    additionalEventData={"MFAUsed": call.get("mfa", "Yes"), "LoginTo": "https://console.aws.amazon.com/console/home"})
        if call.get("result") == "Failure":
            item["errorMessage"] = call.get("error_message", "Failed authentication")
    if call.get("error"):
        item.update(errorCode=call["error"], errorMessage=call.get("error_message", ""))
    return {key: value for key, value in item.items() if value is not None}


def day_records(spec, account, day, back):
    """Every record of a day of the demo, the same each time it is made."""
    rng = random.Random(f"trail:{day}")
    principals = spec["principals"]
    found = []
    for routine in spec["routine"]:
        if routine.get("weekdays") and day.weekday() >= 5:
            continue
        principal = principals[routine["principal"]]
        low, high = routine["hours"]
        runs = rng.randint(*routine["runs"])
        for run in range(runs):
            hour = low + run if runs == high - low + 1 else rng.randint(low, high)
            start = datetime(day.year, day.month, day.day, hour, rng.randint(0, 59), rng.randint(0, 59), tzinfo=timezone.utc)
            if routine.get("signin"):
                found.append(record(principal, account, {"source": "signin", "region": "us-east-1"}, "ConsoleLogin", start, rng))
            for call in routine["calls"]:
                if rng.random() > call.get("chance", 1):
                    continue
                for _ in range(call["per_run"]):
                    moment = start + timedelta(seconds=rng.randint(5, 600))
                    found.append(record(principal, account, call, rng.choice(call["names"]), moment, rng))
    for incident in spec.get("incidents", []):
        if incident["days_ago"] != back:
            continue
        hour, minute = (int(part) for part in incident["time"].split(":"))
        start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
        principal = principals[incident["principal"]]
        for call in incident["calls"]:
            moment = start + timedelta(minutes=call.get("after", 0))
            for number in range(call.get("count", 1)):
                found.append(record(principal, account, call, call["name"], moment + timedelta(seconds=20 * number), rng))
    return sorted(found, key=lambda item: item["eventTime"])


def learn(since):
    """Moves the start of the learning of the principals."""
    for _ in range(trail.RETRIES):
        item = common.get("TRAIL", "KNOWN") or {"pk": "TRAIL", "sk": "KNOWN", "principals": {}, "logins": {}}
        item["since"] = since
        if common.save_versioned(item, int(item.get("version", 0))):
            return
    raise RuntimeError("known principals not written")


def write_trail(seed, now):
    """The days of the seed, oldest first, a day as one log file. The days
    before learned_days_ago only teach the principals; the incidents after
    are news. The combinations of the past days are folded, as the daily job
    would."""
    spec = seed["trail"]
    today = now.date()
    learn(common.iso(now))
    total = 0
    for back in range(int(spec["days"]) - 1, -1, -1):
        day = today - timedelta(days=back)
        if back == int(spec.get("learned_days_ago", 0)):
            learn(common.iso(now - timedelta(days=LEARNED_BEFORE)))
        records = [item for item in day_records(spec, seed["account"], day, back) if item["eventTime"] <= common.iso(now)]
        if records:
            trail.process(records, f"seed-{day}")
            total += len(records)
        if back:
            combos.compact(day.isoformat())
    acknowledge(spec)
    return total


def add_records(seed, since, now):
    """The records of the time since the last reset, as one log file."""
    spec = seed["trail"]
    total = 0
    day = since.date()
    while day <= now.date():
        back = (now.date() - day).days
        records = [item for item in day_records(spec, seed["account"], day, back)
                   if common.iso(since) < item["eventTime"] <= common.iso(now)]
        if records:
            trail.process(records, f"seed-{day}-{common.iso(now)}")
            total += len(records)
        day += timedelta(days=1)
    return total


def acknowledge(spec):
    owner = os.environ["DEMO_EMAIL"].lower()
    rules = set(spec.get("acknowledged", []))
    for item in common.query_all("FINDINGS"):
        if item["rule"] in rules and not item.get("ack"):
            item.update(ack=True, ack_by=owner, ack_at=item["time"])
            common.table().put_item(Item=item)


# What visitors change ------------------------------------------------------------------

def restore(seed, owner):
    """The rules, the mails a user wants, the loads and the findings as the
    reset made them."""
    count = 0
    for key in ({"pk": "SETTINGS", "sk": "RULES"},):
        if common.get(key["pk"], key["sk"]):
            common.table().delete_item(Key=key)
            count += 1
    for item in common.query_all("BACKFILL"):
        common.table().delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
        count += 1
    for email in {owner, *user_emails()}:
        for item in common.query_all("USER#" + email):
            common.table().delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
            count += 1
    rules = set(seed["trail"].get("acknowledged", []))
    for item in common.query_all("FINDINGS"):
        wanted = item["rule"] in rules
        if bool(item.get("ack")) != wanted:
            item.update(ack=wanted, ack_by=owner if wanted else "", ack_at=item["time"] if wanted else "")
            common.table().put_item(Item=item)
            count += 1
    return count


def user_emails():
    found = []
    cognito = common.client("cognito-idp")
    for page in cognito.get_paginator("list_users").paginate(UserPoolId=os.environ["USER_POOL_ID"]):
        for user in page.get("Users", []):
            found.append(next((item["Value"] for item in user.get("Attributes", []) if item["Name"] == "email"), "").lower())
    return [email for email in found if email]


def reset_users(owner):
    """The demo user as deployed, and no one else."""
    pool = os.environ["USER_POOL_ID"]
    cognito = common.client("cognito-idp")
    found = False
    for page in cognito.get_paginator("list_users").paginate(UserPoolId=pool):
        for user in page.get("Users", []):
            email = next((item["Value"] for item in user.get("Attributes", []) if item["Name"] == "email"), "").lower()
            if email == owner:
                found = True
                restore_demo_user(cognito, pool, user["Username"])
            else:
                cognito.admin_delete_user(UserPoolId=pool, Username=user["Username"])
    if not found:
        logger.warning("the demo user %s is missing; the next deploy makes it again", owner)


def restore_demo_user(cognito, pool, username):
    password = common.client("ssm").get_parameter(Name=os.environ["DEMO_PASSWORD_PARAMETER"], WithDecryption=True)["Parameter"]["Value"]
    cognito.admin_enable_user(UserPoolId=pool, Username=username)
    cognito.admin_set_user_password(UserPoolId=pool, Username=username, Password=password, Permanent=True)
    cognito.admin_set_user_mfa_preference(UserPoolId=pool, Username=username,
                                          SoftwareTokenMfaSettings={"Enabled": False, "PreferredMfa": False})
    try:
        cognito.admin_delete_user_attributes(UserPoolId=pool, Username=username, UserAttributeNames=["locale"])
    except Exception as exc:  # noqa: BLE001 - no locale to delete is fine
        logger.info("locale of the demo user not deleted: %s", exc)
    cognito.admin_add_user_to_group(UserPoolId=pool, Username=username, GroupName=os.environ.get("ADMIN_GROUP", "admins"))
    cognito.admin_user_global_sign_out(UserPoolId=pool, Username=username)
