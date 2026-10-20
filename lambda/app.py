"""Overview of the AWS account: costs, budgets and CloudTrail.

One function does all of it:

- EventBridge passes on a new manifest of the Cost and Usage Report from the
  reports bucket (costs.py) and every new log file of CloudTrail (trail.py);
- the daily job ({"action": "daily"}) reads the budgets, looks for cost
  spikes and new services, and mails the digest of the day before (mail.py);
- "Refresh" on the page ({"action": "refresh"}) reads the latest reports and
  the budgets again;
- the page calls /api/... through API Gateway with a Cognito token.

The page reads the table only, never S3 or the AWS APIs: what it shows is
what the reports and the logs left there, so the demo (DEMO=true) fills the
table with the made-up account of reset.py instead and reads nothing of the
account it runs in.

API: /me and the mails a user wants (/me/prefs); /overview; /costs (the
months) and /costs/<YYYY-MM>; /budgets; /trail/days?from=&to= (counters),
/trail/events?day=&cursor=&errors=1&writes=1&q= and any of
principal, source, name, region, ip, error, /trail/summary?from=&to=&day= with
the same filter, and /trail/events/<day>/<sk>;
/findings?cursor=&severity=; /rules; /version. Administrators, the members
of the Cognito group ADMIN_GROUP, also acknowledge findings
(PATCH /findings), set the rules (/admin/settings), start "Refresh"
(POST /admin/refresh), load a period from S3 again (/admin/backfill,
backfill.py) and manage the accounts at /admin/users.
"""

import html.parser
import json
import os
import re
import secrets
import string
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, unquote_plus

import backfill
import combos
import common
import costs
import mail
import rules
import sso
import trail

EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
USERNAME_RE = re.compile(r"[\w.@+=,-]{1,128}")
# A user of the pool made by a sign-in through AWS SSO.
EXTERNAL = "EXTERNAL_PROVIDER"
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
PERIOD_RE = re.compile(r"\d{4}-\d{2}")
REGION_RE = re.compile(r"[a-z]{2}(-[a-z]+)+-\d")
PATTERN_RE = re.compile(r"[\w*?\-./:@|+=,]{1,200}")
EVENTS_PAGE = 200
EVENTS_READS = 10
# Fields of an event the list can be narrowed to.
EVENT_FIELDS = ("principal", "source", "name", "region", "ip", "error")
FINDINGS_PAGE = 100
TRAIL_DAYS_MAX = 92

ERRORS = {
    "not_found": "Not found",
    "email_not_verified": "Email not verified",
    "admin_only": "Administrators only",
    "bad_json": "Invalid JSON",
    "bad_json_object": "A JSON object was expected",
    "too_large": "Request too large",
    "internal": "Internal error",
    "bad_flag": "A switch must be on or off",
    "bad_day": "A day such as 2026-10-18 is needed",
    "bad_period": "A month such as 2026-10 is needed",
    "bad_range": "At most 92 days, the first before the last",
    "bad_rule": "Unknown rule",
    "bad_region": "Unknown region",
    "bad_pattern": "Patterns: letters, digits and - _ . / : @ * ?, up to 200 characters, 50 at most",
    "finding_not_found": "Finding not found",
    "event_not_found": "Event not found",
    "bad_email": "An email address is needed",
    "user_exists": "This user already exists",
    "user_not_found": "No such user",
    "not_yourself": "You cannot do this to your own account",
    "sso_user": "IAM Identity Center manages this user: its password, two-factor sign-in and administrator role",
    "bad_backfill_range": "Days up to yesterday, the first before the last, at most 1100 of them",
    "bad_keep": "Keep for 1 to 3650 days",
    "bad_backfill_kinds": "Pick the CloudTrail logs, the costs or both",
    "backfill_running": "Another load is running",
    "backfill_not_found": "No such load",
    "backfill_no_source": "The bucket of the CloudTrail logs or of the cost reports is not connected",
    "demo_readonly": "Not available in the demo",
}


class ApiError(Exception):
    def __init__(self, status, code, detail=""):
        super().__init__(ERRORS[code])
        self.status = status
        self.code = code
        self.message = ERRORS[code]
        self.detail = detail


def handler(event, context):
    if not isinstance(event, dict):
        return {"ok": True}
    if str(event.get("triggerSource", "")).startswith("TokenGeneration_"):
        return sso.token(event)
    if event.get("action") == "daily":
        return daily()
    if event.get("action") == "refresh":
        return costs.refresh()
    if event.get("action") == "backfill":
        return backfill.run(str(event.get("job") or ""), context)
    if event.get("source") == "aws.s3" and not demo():
        detail = event.get("detail") or {}
        bucket = (detail.get("bucket") or {}).get("name", "")
        key = unquote_plus((detail.get("object") or {}).get("key", ""))
        if bucket == os.environ.get("REPORTS_BUCKET"):
            return costs.handle(bucket, key)
        if bucket == os.environ.get("TRAIL_BUCKET"):
            return trail.handle(bucket, key)
        return {"skipped": bucket}
    if "requestContext" in event:
        return api(event)
    return {"ok": True}


def daily():
    """Budgets, the checks of the costs and the digest of yesterday. A step
    that fails does not keep the next from running."""
    result = {}
    today = common.day_of(common.now_utc())
    steps = (("budgets", costs.refresh_budgets),) if costs.budgets_on() else ()
    for name, step in (*steps, ("findings", lambda: len(costs.check(today))),
                       ("digest", lambda: mail.send_digest(common.days_before(today, 1))),
                       ("combinations", lambda: combos.compact_recent(today))):
        try:
            value = step()
            result[name] = value if isinstance(value, int) else "ok"
        except Exception:
            common.logger.exception("daily step %s failed", name)
            result[name] = "error"
    return result


# API ----------------------------------------------------------------------------

def api(event):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    parts = [unquote(part) for part in (event.get("rawPath") or "/").split("/") if part]
    try:
        if parts[:1] != ["api"]:
            raise ApiError(404, "not_found")
        me = actor(event)
        result = route(method, parts[1:], event, me)
    except ApiError as exc:
        result = response(exc.status, {"error": exc.message, "code": exc.code, **({"detail": exc.detail} if exc.detail else {})})
    except json.JSONDecodeError:
        result = response(400, {"error": ERRORS["bad_json"], "code": "bad_json"})
    except Exception:
        common.logger.exception("unhandled")
        result = response(500, {"error": ERRORS["internal"], "code": "internal"})
    common.logger.info("%s %s -> %s", method, event.get("rawPath"), result["statusCode"])
    return result


def route(method, parts, event, me):
    admin = is_admin(event)
    if parts == ["me"] and method == "GET":
        return response(200, {"email": me, "admin": admin, "demo": demo(), "prefs": mail.prefs(me), "mail": bool(os.environ.get("SENDER")),
                              "account": os.environ.get("ACCOUNT_ID", ""), "rules": rule_list(),
                              "sources": {"costs": bool(os.environ.get("REPORTS_BUCKET")), "trail": bool(os.environ.get("TRAIL_BUCKET"))}})
    if parts == ["me", "prefs"] and method == "PUT":
        return response(200, save_prefs(me, body_json(event)))
    if parts == ["overview"] and method == "GET":
        return response(200, overview())
    if parts == ["costs"] and method == "GET":
        return response(200, {"periods": [item["sk"] for item in common.query_all("COST")]})
    if len(parts) == 2 and parts[0] == "costs" and method == "GET":
        if not PERIOD_RE.fullmatch(parts[1]):
            raise ApiError(400, "bad_period")
        month = common.get("COST", parts[1])
        return response(200, public(month) if month else {"period": parts[1], "empty": True})
    if parts == ["budgets"] and method == "GET":
        return response(200, common.get("BUDGETS", "SNAPSHOT") or {"budgets": []})
    if parts == ["trail", "days"] and method == "GET":
        return response(200, {"items": trail_days(query_param(event, "from"), query_param(event, "to"))})
    if parts == ["trail", "summary"] and method == "GET":
        return response(200, trail_summary(event))
    if parts == ["trail", "events"] and method == "GET":
        return response(200, trail_events(event))
    if len(parts) == 4 and parts[:2] == ["trail", "events"] and method == "GET":
        if not DAY_RE.fullmatch(parts[2]):
            raise ApiError(400, "bad_day")
        item = common.get("EVENTS#" + parts[2], parts[3])
        if not item:
            raise ApiError(404, "event_not_found")
        return response(200, public(item))
    if parts == ["findings"] and method == "GET":
        return response(200, findings(query_param(event, "cursor"), query_param(event, "severity")))
    if parts == ["findings"] and method == "PATCH":
        require(admin)
        return response(200, acknowledge(body_json(event), me))
    if parts == ["rules"] and method == "GET":
        return response(200, {"rules": rule_list(), "settings": common.rules_settings()})
    if parts == ["version"] and method == "GET":
        return response(200, version_info())
    if parts[:1] == ["admin"]:
        require(admin)
        return admin_route(method, parts[1:], event, me)
    raise ApiError(404, "not_found")


def require(admin):
    if not admin:
        raise ApiError(403, "admin_only")


def rule_list():
    return [{"id": rule, "severity": severity, "source": source} for rule, (severity, source) in rules.RULES.items()]


def public(item):
    return {key: value for key, value in common.plain(item).items() if key not in ("pk", "version", "expires")}


# Overview ---------------------------------------------------------------------

def overview():
    today = common.now_utc()
    period = today.strftime("%Y-%m")
    previous = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    month = common.get("COST", period) or {}
    before = common.get("COST", previous) or {}
    since = common.iso(today - timedelta(days=7))
    recent = common.query_all("FINDINGS", start=since)
    first = common.days_before(common.day_of(today), 13)
    return {
        "today": common.day_of(today),
        "period": period,
        "month": public(month) if month else None,
        "previous": {"period": previous, "total": common.plain(before.get("total", 0)), "days": common.plain(before.get("usage_days") or {})} if before else None,
        "budgets": public(common.get("BUDGETS", "SNAPSHOT") or {"budgets": []}),
        "findings": {
            "critical": sum(1 for item in recent if item["severity"] == rules.CRITICAL),
            "warning": sum(1 for item in recent if item["severity"] == rules.WARNING),
            "open": sum(1 for item in recent if not item.get("ack")),
            "latest": [public(item) for item in sorted(recent, key=lambda item: item["sk"], reverse=True)[:8]],
        },
        "trail": trail_days(first, common.day_of(today)),
    }


# CloudTrail -------------------------------------------------------------------

def trail_days(first, last):
    today = common.day_of(common.now_utc())
    last = last or today
    first = first or common.days_before(last, 13)
    if not DAY_RE.fullmatch(first) or not DAY_RE.fullmatch(last) or first > last:
        raise ApiError(400, "bad_range")
    if (datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")).days >= TRAIL_DAYS_MAX:
        raise ApiError(400, "bad_range")
    items = common.query_all("TRAIL", start="DAY#" + first, end="DAY#" + last)
    return [public(item) for item in items]


def filter_params(event):
    """The filter of the page: only errors, only changes, the values clicked
    (principal, service, event, region, address, error code), all of them at
    once, and a text that any of them may hold."""
    fields = {name: query_param(event, name) for name in EVENT_FIELDS if query_param(event, name)}
    return fields, query_param(event, "errors") == "1", query_param(event, "writes") == "1", query_param(event, "q")[:200]


def event_filter(event):
    fields, errors_only, writes_only, text = filter_params(event)
    text = text.lower()

    def keep(item):
        if errors_only and not item.get("error"):
            return False
        if writes_only and (item.get("read_only") or item.get("name") == "ConsoleLogin"):
            return False
        if text and not any(text in str(item.get(name, "")).lower() for name in (*EVENT_FIELDS, "arn")):
            return False
        return all(str(item.get(name, "")) == value for name, value in fields.items())

    return keep


def trail_events(event):
    day = query_param(event, "day") or common.day_of(common.now_utc())
    if not DAY_RE.fullmatch(day):
        raise ApiError(400, "bad_day")
    pk = "EVENTS#" + day
    keep = event_filter(event)

    # The table is read a page at a time; a narrow filter reads on until a page
    # is full or EVENTS_READS pages are read, and the cursor goes on from there.
    cursor = common.decode_cursor(query_param(event, "cursor"), pk)
    found = []
    for _ in range(EVENTS_READS):
        items, cursor = common.query(pk, desc=True, limit=EVENTS_PAGE, cursor=cursor)
        found += [item for item in items if keep(item)]
        if not cursor or len(found) >= EVENTS_PAGE:
            break
    return {"day": day, "items": [public(item) for item in found], "next_cursor": common.encode_cursor(cursor)}


def trail_summary(event):
    """The calls a filter keeps, from the combinations (combos.py): a total
    per day of from…to, and for day the tiles and the lists of the page."""
    last = query_param(event, "to") or common.day_of(common.now_utc())
    first = query_param(event, "from") or common.days_before(last, 13)
    day = query_param(event, "day") or last
    if not all(DAY_RE.fullmatch(value) for value in (first, last, day)) or first > last:
        raise ApiError(400, "bad_range")
    if (datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")).days >= TRAIL_DAYS_MAX:
        raise ApiError(400, "bad_range")
    fields, errors_only, writes_only, text = filter_params(event)
    return combos.summary(first, last, day, combos.matcher(fields, errors_only, writes_only, text))


# Findings ---------------------------------------------------------------------

def findings(cursor, severity):
    key = common.decode_cursor(cursor, "FINDINGS")
    items, last = common.query("FINDINGS", desc=True, limit=FINDINGS_PAGE, cursor=key)
    if severity in (rules.CRITICAL, rules.WARNING):
        items = [item for item in items if item["severity"] == severity]
    return {"items": [public(item) for item in items], "next_cursor": common.encode_cursor(last)}


def acknowledge(payload, me):
    sk = str(payload.get("sk") or "")
    if not isinstance(payload.get("ack"), bool):
        raise ApiError(400, "bad_flag")
    item = common.get("FINDINGS", sk) if sk else None
    if not item:
        raise ApiError(404, "finding_not_found")
    item.update({"ack": payload["ack"], "ack_by": me if payload["ack"] else "", "ack_at": common.now_iso() if payload["ack"] else ""})
    common.table().put_item(Item=item)
    return public(item)


# Settings ---------------------------------------------------------------------

def save_prefs(me, payload):
    current = mail.prefs(me)
    for flag in ("digest", "alerts"):
        if flag in payload:
            if not isinstance(payload[flag], bool):
                raise ApiError(400, "bad_flag")
            current[flag] = payload[flag]
    common.table().put_item(Item={"pk": "USER#" + me, "sk": "PREFS", **current, "updated_at": common.now_iso()})
    return current


def save_settings(payload, me):
    current = common.rules_settings()
    if "muted" in payload:
        muted = payload["muted"]
        if not isinstance(muted, list) or any(rule not in rules.RULES for rule in muted):
            raise ApiError(400, "bad_rule")
        current["muted"] = sorted(set(muted))
    if "regions" in payload:
        regions = payload["regions"]
        if not isinstance(regions, list) or len(regions) > 40 or any(not isinstance(region, str) or not REGION_RE.fullmatch(region) for region in regions):
            raise ApiError(400, "bad_region")
        current["regions"] = sorted(set(regions))
    if "ignored" in payload:
        patterns = payload["ignored"]
        if not isinstance(patterns, list) or len(patterns) > 50 or any(not isinstance(item, str) or not PATTERN_RE.fullmatch(item) for item in patterns):
            raise ApiError(400, "bad_pattern")
        current["ignored"] = list(dict.fromkeys(patterns))
    current.update({"updated_at": common.now_iso(), "updated_by": me})
    common.table().put_item(Item={"pk": "SETTINGS", "sk": "RULES", **current})
    return current


# Administration -------------------------------------------------------------

def admin_route(method, parts, event, me):
    # The demo shows the users and the loads; changing the users, or reading
    # an account it does not look at, is not for visitors.
    if demo() and method != "GET" and parts[:1] in (["users"], ["refresh"], ["backfill"]):
        raise ApiError(403, "demo_readonly")
    if parts == ["settings"] and method == "PUT":
        return response(200, save_settings(body_json(event), me))
    if parts == ["refresh"] and method == "POST":
        common.client("lambda").invoke(FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"], InvocationType="Event",
                                       Payload=json.dumps({"action": "refresh"}).encode("utf-8"))
        return response(202, {"started": True})
    if parts == ["backfill"] and method == "GET":
        return response(200, {"items": [public(job) for job in backfill.jobs()], "event_days": int(os.environ.get("EVENT_DAYS", "90"))})
    if parts == ["backfill"] and method == "POST":
        return response(202, public(start_backfill(body_json(event), me)))
    if parts == ["backfill"] and method == "DELETE":
        job = backfill.set_status(query_param(event, "id"), "stopped") if query_param(event, "id") else None
        if not job:
            raise ApiError(404, "backfill_not_found")
        return response(200, public(job))
    if parts == ["users"] and method == "GET":
        return response(200, {"items": list_users(me)})
    if parts == ["users"] and method == "POST":
        payload = body_json(event)
        return response(201, create_user(clean_email(payload.get("email")), payload.get("admin") is True))
    if parts == ["users"] and method == "PUT":
        return response(200, update_user(body_json(event), me))
    if parts == ["users"] and method == "DELETE":
        email = clean_email(query_param(event, "email"))
        if email == me:
            raise ApiError(400, "not_yourself")
        return response(200, delete_user(email, query_param(event, "username")))
    raise ApiError(404, "not_found")


# The days first…last of the logs, the months they touch of the report, or
# both, kept keep_days; today stays with the live loading.
def start_backfill(payload, me):
    first, last = str(payload.get("from") or ""), str(payload.get("to") or "")
    yesterday = common.days_before(common.day_of(common.now_utc()), 1)
    if not DAY_RE.fullmatch(first) or not DAY_RE.fullmatch(last) or not first <= last <= yesterday:
        raise ApiError(400, "bad_backfill_range")
    if (datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")).days >= backfill.MAX_DAYS:
        raise ApiError(400, "bad_backfill_range")
    keep = payload.get("keep_days")
    if isinstance(keep, bool) or not isinstance(keep, int) or not 1 <= keep <= backfill.KEEP_DAYS_MAX:
        raise ApiError(400, "bad_keep")
    logs, reports = payload.get("trail"), payload.get("costs")
    if not isinstance(logs, bool) or not isinstance(reports, bool):
        raise ApiError(400, "bad_flag")
    if not logs and not reports:
        raise ApiError(400, "bad_backfill_kinds")
    if (logs and not os.environ.get("TRAIL_BUCKET")) or (reports and not os.environ.get("REPORTS_BUCKET")):
        raise ApiError(400, "backfill_no_source")
    if backfill.running():
        raise ApiError(409, "backfill_running")
    return backfill.start(first, last, keep, logs, reports, me)


# Only a verified email from the Cognito token counts, and only the groups
# Cognito put into it.
def actor(event):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    address = str(claims.get("email") or "").lower()
    if not address or str(claims.get("email_verified")).lower() != "true":
        raise ApiError(403, "email_not_verified")
    return address


def is_admin(event):
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    groups = claims.get("cognito:groups") or []
    if isinstance(groups, str):
        # API Gateway passes a list claim as a string such as "[admins other]".
        groups = groups.strip("[]").replace(",", " ").split()
    return admin_group() in groups


def admin_group():
    return os.environ.get("ADMIN_GROUP", "admins")


def cognito():
    return common.client("cognito-idp")


def list_users(me):
    pool = os.environ["USER_POOL_ID"]
    admins = set()
    for page in cognito().get_paginator("list_users_in_group").paginate(UserPoolId=pool, GroupName=admin_group()):
        admins.update(user_email(user) for user in page.get("Users", []))
    users = []
    for page in cognito().get_paginator("list_users").paginate(UserPoolId=pool):
        for user in page.get("Users", []):
            email = user_email(user)
            external = user.get("UserStatus") == EXTERNAL
            users.append({
                "email": email,
                "username": user.get("Username", ""),
                "sso": external,
                "status": user.get("UserStatus", ""),
                "enabled": bool(user.get("Enabled", True)),
                "created": common.iso(user["UserCreateDate"]) if user.get("UserCreateDate") else "",
                # A user of AWS SSO is an administrator by their groups in
                # IAM Identity Center, and signs in with its two-factor check.
                "admin": sso_admin(email) if external else email in admins,
                "mfa": False if external else has_mfa(pool, user.get("Username")),
                "prefs": mail.prefs(email),
                "self": email == me,
            })
    users.sort(key=lambda user: (user["email"], user["sso"]))
    return users


def sso_admin(email):
    try:
        return sso.admin(email)
    except Exception:
        common.logger.exception("groups of %s not read", email)
        return False


def target_user(pool, email, username):
    """The user an action is for: by its username when the page names one (a
    user of AWS SSO, or one of two with the same email), else by email; and
    whether IAM Identity Center manages it."""
    if username and not USERNAME_RE.fullmatch(username):
        raise ApiError(400, "user_not_found")
    try:
        user = cognito().admin_get_user(UserPoolId=pool, Username=username or email)
    except Exception as exc:
        if common.error_code(exc) == "UserNotFoundException":
            raise ApiError(404, "user_not_found") from None
        raise
    found = {item.get("Name"): item.get("Value", "") for item in user.get("UserAttributes", [])}
    if (sso.email_of(found) or email) != email:
        raise ApiError(404, "user_not_found")
    return user.get("Username", username or email), user.get("UserStatus") == EXTERNAL


# ListUsers does not say whether a user has an authenticator app, so each user
# is asked for separately. There are a handful of users.
def has_mfa(pool, username):
    user = cognito().admin_get_user(UserPoolId=pool, Username=username)
    return "SOFTWARE_TOKEN_MFA" in (user.get("UserMFASettingList") or [])


def create_user(email, admin):
    pool = os.environ["USER_POOL_ID"]
    password = temporary_password()
    try:
        cognito().admin_create_user(
            UserPoolId=pool,
            Username=email,
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
            TemporaryPassword=password,
            DesiredDeliveryMediums=["EMAIL"],
        )
    except Exception as exc:
        if common.error_code(exc) == "UsernameExistsException":
            raise ApiError(409, "user_exists") from None
        raise
    if admin:
        cognito().admin_add_user_to_group(UserPoolId=pool, Username=email, GroupName=admin_group())
    return {"email": email, "temporary_password": password}


def update_user(payload, me):
    email = clean_email(payload.get("email"))
    pool = os.environ["USER_POOL_ID"]
    own = email == me
    result = {"email": email}
    for flag in ("enabled", "admin", "reset_mfa", "reset_password"):
        if flag in payload and not isinstance(payload[flag], bool):
            raise ApiError(400, "bad_flag")
    username, external = target_user(pool, email, str(payload.get("username") or ""))
    if external and any(flag in payload for flag in ("admin", "reset_mfa", "reset_password")):
        raise ApiError(400, "sso_user")
    try:
        if "enabled" in payload:
            if own and not payload["enabled"]:
                raise ApiError(400, "not_yourself")
            if payload["enabled"]:
                cognito().admin_enable_user(UserPoolId=pool, Username=username)
            else:
                cognito().admin_disable_user(UserPoolId=pool, Username=username)
        if "admin" in payload:
            if own and not payload["admin"]:
                raise ApiError(400, "not_yourself")
            if payload["admin"]:
                cognito().admin_add_user_to_group(UserPoolId=pool, Username=username, GroupName=admin_group())
            else:
                cognito().admin_remove_user_from_group(UserPoolId=pool, Username=username, GroupName=admin_group())
        # A user who lost their phone signs in with the password alone and can
        # set up the app again in the settings.
        if payload.get("reset_mfa"):
            cognito().admin_set_user_mfa_preference(
                UserPoolId=pool,
                Username=username,
                SoftwareTokenMfaSettings={"Enabled": False, "PreferredMfa": False},
            )
        if payload.get("reset_password"):
            password = temporary_password()
            cognito().admin_set_user_password(UserPoolId=pool, Username=username, Password=password, Permanent=False)
            result["temporary_password"] = password
    except ApiError:
        raise
    except Exception as exc:
        if common.error_code(exc) == "UserNotFoundException":
            raise ApiError(404, "user_not_found") from None
        raise
    return result


# The account goes, and with it the mails it wanted, unless another user with
# the same email (a password and AWS SSO) stays.
def delete_user(email, username=""):
    pool = os.environ["USER_POOL_ID"]
    try:
        name, _ = target_user(pool, email, username)
        cognito().admin_delete_user(UserPoolId=pool, Username=name)
    except ApiError:
        pass
    for page in cognito().get_paginator("list_users").paginate(UserPoolId=pool):
        if any(user_email(user) == email for user in page.get("Users", [])):
            return {"deleted": email, "items": 0}
    items = common.query_all("USER#" + email)
    for item in items:
        common.table().delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    return {"deleted": email, "items": len(items)}


PASSWORD_SYMBOLS = "!@#%&*-_=+"


def temporary_password():
    alphabet = string.ascii_letters + string.digits + PASSWORD_SYMBOLS
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(16))
        if (any(c.islower() for c in password) and any(c.isupper() for c in password)
                and any(c.isdigit() for c in password) and any(c in PASSWORD_SYMBOLS for c in password)):
            return password


def user_email(user):
    attributes = {item.get("Name"): item.get("Value", "") for item in user.get("Attributes", [])}
    # A user of AWS SSO may have the address only as the SAML subject.
    return sso.email_of(attributes) or attributes.get("email", "").lower() or user.get("Username", "")


def clean_email(value):
    email = str(value or "").strip().lower()
    if not EMAIL_RE.fullmatch(email) or len(email) > 254:
        raise ApiError(400, "bad_email")
    return email


# Versions -----------------------------------------------------------------
# The version of this deployment (VERSION) and the releases of the demo
# repository (RELEASES_REPOSITORY, owner/name) that came after it. They come
# from the Atom feed of the repository's GitHub releases, a public page that
# the hourly limit of the REST API, shared by every function behind Lambda's
# addresses, does not touch. The list stays an hour in the warm function, and
# the last one read serves while GitHub does not answer.

RELEASES_SECONDS = 3600
VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
ATOM = "{http://www.w3.org/2005/Atom}"
_releases = None


def version_info():
    current = os.environ.get("VERSION", "")
    repository = os.environ.get("RELEASES_REPOSITORY", "")
    info = {
        "version": current,
        "repository": repository,
        "url": f"https://github.com/{repository}/releases" if repository else None,
        "latest": None,
        "newer": [],
        "checked_at": None,
        "error": None,
    }
    if not repository:
        return info
    try:
        releases, checked_at = github_releases(repository)
    except Exception:
        common.logger.exception("releases of %s not read", repository)
        info["error"] = "unavailable"
        return info
    info["checked_at"] = checked_at
    info["latest"] = releases[0]["version"] if releases else None
    info["newer"] = [release for release in releases if version_key(release["version"]) > version_key(current)]
    return info


def version_key(value):
    match = VERSION_RE.match(value or "")
    return tuple(int(part) for part in match.groups()) if match else ()


def github_releases(repository):
    global _releases
    now = time.time()
    if _releases and _releases[0] == repository and now - _releases[1] < RELEASES_SECONDS:
        return _releases[2], _releases[3]
    try:
        request = urllib.request.Request(
            f"https://github.com/{repository}/releases.atom",
            headers={"Accept": "application/atom+xml", "User-Agent": "release-check (+https://github.com/" + repository + ")"},
        )
        with urllib.request.urlopen(request, timeout=5) as answer:
            releases = parse_releases(answer.read(2_000_000))
    except Exception:
        if _releases and _releases[0] == repository:
            common.logger.warning("releases of %s not read; the list of %s serves", repository, _releases[3], exc_info=True)
            return _releases[2], _releases[3]
        raise
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    _releases = (repository, now, releases, checked_at)
    return releases, checked_at


# The entries of the feed whose tag is a version, newest version first, with
# their notes as plain text.
def parse_releases(feed):
    import xml.etree.ElementTree as ElementTree

    releases = []
    for entry in ElementTree.fromstring(feed).findall(ATOM + "entry"):
        link = entry.find(ATOM + "link")
        url = link.get("href", "") if link is not None else ""
        version = unquote(url.rstrip("/").rsplit("/", 1)[-1])
        if not version_key(version):
            continue
        releases.append({
            "version": version,
            "title": (entry.findtext(ATOM + "title") or version).strip(),
            "date": (entry.findtext(ATOM + "updated") or "")[:10],
            "url": url,
            "notes": release_notes(entry.findtext(ATOM + "content") or "")[:4000],
        })
    releases.sort(key=lambda release: version_key(release["version"]), reverse=True)
    return releases


class ReleaseNotes(html.parser.HTMLParser):
    BREAKS = {"p", "div", "br", "ul", "ol", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.parts.append("\n• ")
        elif tag in self.BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def release_notes(value):
    parser = ReleaseNotes()
    parser.feed(value)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


# Helpers ------------------------------------------------------------------

# The public demo (DEMO=true): its visitors share one account, and the page
# shows the examples of the reset instead of an AWS account.
def demo():
    return os.environ.get("DEMO", "false") == "true"


def query_param(event, name):
    return ((event.get("queryStringParameters") or {}).get(name) or "").strip()


def body_json(event, limit=20_000):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf-8")
    if len(raw) > limit:
        raise ApiError(413, "too_large")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ApiError(400, "bad_json_object")
    return payload


def response(status, payload):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"},
        "body": json.dumps(common.plain(payload), ensure_ascii=False),
    }
