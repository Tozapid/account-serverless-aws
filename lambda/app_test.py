import json
import os
import unittest
from datetime import timedelta

import app
import backfill
import combos
import common
import costs
import fakes
import mail
import rules
import trail

OWNER = "owner@example.com"
TRAIL_BUCKET = "trail-bucket"
REPORTS_BUCKET = "reports-bucket"
PREFIX = "cur/account-serverless"


def record(name, source="iam", who=None, time="2026-10-17T10:00:00Z", event_id=None, **fields):
    identity = who or {"type": "AssumedRole", "arn": "arn:aws:sts::1:assumed-role/Admin/me@example.com",
                       "sessionContext": {"sessionIssuer": {"userName": "Admin"}}}
    return {"eventTime": time, "eventSource": source + ".amazonaws.com", "eventName": name, "userIdentity": identity,
            "awsRegion": "eu-central-1", "sourceIPAddress": "203.0.113.5", "eventID": event_id or f"{name}-{time}",
            "readOnly": False, **fields}


ROOT = {"type": "Root", "arn": "arn:aws:iam::1:root"}


class Base(unittest.TestCase):
    def setUp(self):
        self.fakes = fakes.install()
        self.table = self.fakes["table"]
        os.environ.update({"TABLE_NAME": "aws", "USER_POOL_ID": "pool", "ACCOUNT_ID": "1", "SENDER": "alert@aws.example.com",
                           "SITE_URL": "https://aws.example.com", "REPORTS_BUCKET": REPORTS_BUCKET, "TRAIL_BUCKET": TRAIL_BUCKET,
                           "CUR_PREFIX": PREFIX, "COST_TAG": "Project", "SPIKE_FACTOR": "2", "SPIKE_MIN_USD": "0.5",
                           "ALLOWED_REGIONS": "eu-central-1,us-east-1", "EVENT_DAYS": "90", "ADMIN_GROUP": "admins",
                           "AWS_LAMBDA_FUNCTION_NAME": "account-serverless"})
        self.fakes["cognito-idp"].add(OWNER)

    def findings(self):
        return [item for (pk, _), item in self.table.items.items() if pk == "FINDINGS"]

    def call(self, method, path, body=None, params=None, admin=False):
        result = app.handler(fakes.event(method, path, body, params, admin=admin), None)
        return result["statusCode"], json.loads(result["body"])

    def learned(self):
        """The trail has been learning for long enough."""
        long_ago = common.iso(common.now_utc() - timedelta(days=30))
        self.table.put_item(Item={"pk": "TRAIL", "sk": "KNOWN", "since": long_ago, "principals": {}, "logins": {}, "version": 1})


class RulesTest(unittest.TestCase):
    def test_principals(self):
        self.assertEqual(rules.principal(record("X")), "role/Admin/me@example.com")
        self.assertEqual(rules.principal(record("X", who=ROOT)), "root")
        self.assertEqual(rules.principal(record("X", who={"type": "IAMUser", "userName": "ci"})), "user/ci")

    def test_checks(self):
        regions = ["eu-central-1", "us-east-1"]
        login = record("ConsoleLogin", "signin", who={"type": "IAMUser", "userName": "bob"}, readOnly=None,
                       responseElements={"ConsoleLogin": "Success"}, additionalEventData={"MFAUsed": "No"})
        self.assertEqual(rules.check(login, regions), ["console_login_no_mfa"])
        failed = dict(login, responseElements={"ConsoleLogin": "Failure"})
        self.assertEqual(rules.check(failed, regions), ["console_login_failed"])
        self.assertEqual(rules.check(record("StopLogging", "cloudtrail"), regions), ["trail_tampering"])
        self.assertEqual(rules.check(record("CreateAccessKey"), regions), ["iam_credentials"])
        self.assertEqual(rules.check(record("AttachRolePolicy", requestParameters={"policyArn": "arn:aws:iam::aws:policy/AdministratorAccess"}), regions), ["iam_admin"])
        self.assertEqual(rules.check(record("TagRole"), regions), ["iam_change"])
        policy = json.dumps({"Statement": [{"Effect": "Allow", "Principal": "*", "Action": "s3:GetObject", "Resource": "*"}]})
        self.assertEqual(rules.check(record("PutBucketPolicy", "s3", requestParameters={"bucketPolicy": json.loads(policy)}), regions), ["public_access"])
        ssh = {"ipPermissions": {"items": [{"ipProtocol": "tcp", "fromPort": 22, "toPort": 22, "ipRanges": {"items": [{"cidrIp": "0.0.0.0/0"}]}}]}}
        web = {"ipPermissions": {"items": [{"ipProtocol": "tcp", "fromPort": 443, "toPort": 443, "ipRanges": {"items": [{"cidrIp": "0.0.0.0/0"}]}}]}}
        self.assertEqual(rules.check(record("AuthorizeSecurityGroupIngress", "ec2", requestParameters=ssh), regions), ["open_port"])
        self.assertEqual(rules.check(record("AuthorizeSecurityGroupIngress", "ec2", requestParameters=web), regions), [])
        self.assertEqual(rules.check(dict(record("RunInstances", "ec2"), awsRegion="ap-south-1"), regions), ["unusual_region"])
        # A call that failed changed nothing; a read changes nothing.
        self.assertEqual(rules.check(record("CreateAccessKey", errorCode="AccessDenied"), regions), [])
        self.assertEqual(rules.check(record("ListUsers", readOnly=True), regions), [])
        self.assertEqual(rules.check(record("CreateAccessKey", who={"type": "AWSService", "invokedBy": "x"}), regions), [])


class TrailTest(Base):
    def put_log(self, records, key="AWSLogs/1/CloudTrail/eu-central-1/2026/10/17/a.json.gz"):
        self.fakes["s3"].put_json_gz(TRAIL_BUCKET, key, {"Records": records})
        return app.handler({"source": "aws.s3", "detail": {"bucket": {"name": TRAIL_BUCKET}, "object": {"key": key}}}, None)

    def test_counters_events_and_alert(self):
        records = [
            record("ConsoleLogin", "signin", who=ROOT, readOnly=None, responseElements={"ConsoleLogin": "Success"},
                   additionalEventData={"MFAUsed": "Yes"}),
            record("ListBuckets", "s3", readOnly=True),
            record("PutObject", "s3", errorCode="AccessDenied"),
        ]
        result = self.put_log(records)
        self.assertEqual(result, {"records": 3, "events": 2, "findings": 1})
        day = self.table.items[("TRAIL", "DAY#2026-10-17")]
        self.assertEqual((day["events"], day["writes"], day["errors"], day["signins"]), (3, 1, 1, 1))
        self.assertEqual(day["error_codes"], {"AccessDenied": 1})
        events = [key for key in self.table.items if key[0] == "EVENTS#2026-10-17"]
        self.assertEqual(len(events), 2)
        self.assertEqual(self.findings()[0]["rule"], "root_activity")
        sent = self.fakes["sesv2"].sent
        self.assertEqual((len(sent), sent[0]["to"]), (1, [OWNER]))
        self.assertIn("root", sent[0]["text"])
        # The same file again: counted again, but no second finding or mail.
        self.put_log(records)
        self.assertEqual(self.table.items[("TRAIL", "DAY#2026-10-17")]["events"], 6)
        self.assertEqual((len(self.findings()), len(sent)), (1, 1))

    def test_alerts_once_an_hour(self):
        self.put_log([record("CreateAccessKey", time="2026-10-17T10:00:00Z")])
        self.put_log([record("CreateAccessKey", time="2026-10-17T10:05:00Z")])
        self.assertEqual(len(self.findings()), 2)
        self.assertEqual(len(self.fakes["sesv2"].sent), 1)

    def test_settings_mute_and_ignore(self):
        self.table.put_item(Item={"pk": "SETTINGS", "sk": "RULES", "muted": ["iam_change"], "ignored": ["role/Terraform/*"]})
        terraform = {"type": "AssumedRole", "arn": "arn:aws:sts::1:assumed-role/Terraform/ci",
                     "sessionContext": {"sessionIssuer": {"userName": "Terraform"}}}
        self.put_log([record("TagRole"), record("CreateAccessKey", who=terraform)])
        self.assertEqual(self.findings(), [])

    def test_denied_burst(self):
        self.put_log([record("GetObject", "s3", errorCode="AccessDenied", readOnly=True, event_id=f"d{i}") for i in range(15)])
        self.assertEqual(self.findings(), [])
        self.put_log([record("GetObject", "s3", errorCode="AccessDenied", readOnly=True, event_id=f"e{i}") for i in range(10)])
        self.assertEqual([item["rule"] for item in self.findings()], ["access_denied"])
        self.put_log([record("GetObject", "s3", errorCode="AccessDenied", readOnly=True, event_id=f"f{i}") for i in range(10)])
        self.assertEqual(len(self.findings()), 1)

    def test_race_is_retried(self):
        self.table.conflicts = 2
        self.put_log([record("ListBuckets", "s3", readOnly=True)])
        self.assertEqual(self.table.items[("TRAIL", "DAY#2026-10-17")]["events"], 1)

    def test_new_principal_and_address_after_learning(self):
        # While learning, what is seen is only remembered.
        self.put_log([record("TagRole")])
        self.assertEqual([item["rule"] for item in self.findings()], ["iam_change"])
        self.learned()
        bob = {"type": "IAMUser", "userName": "bob"}
        self.put_log([record("TagRole", who=bob, event_id="t2"),
                      record("ConsoleLogin", "signin", who=bob, readOnly=None, responseElements={"ConsoleLogin": "Success"},
                             additionalEventData={"MFAUsed": "Yes"}, event_id="l1")])
        found = sorted(item["rule"] for item in self.findings())
        self.assertEqual(found, ["iam_change", "iam_change", "new_login_ip", "new_principal"])
        self.put_log([record("TagRole", who=bob, event_id="t3")])
        self.assertEqual(sum(1 for item in self.findings() if item["rule"] == "new_principal"), 1)


class CostsTest(Base):
    def row(self, day, service, cost, kind="Usage", tags=None, usage="Usage"):
        return {"line_item_usage_start_date": day + "T00:00:00Z", "line_item_product_code": service,
                "line_item_usage_type": usage, "line_item_line_item_type": kind, "line_item_unblended_cost": str(cost),
                "line_item_currency_code": "USD", "product_region_code": "eu-central-1",
                "resource_tags": json.dumps(tags or {})}

    def report(self, period, rows, listed=True):
        data = f"{PREFIX}/data/BILLING_PERIOD={period}/account-serverless-00001.csv.gz"
        self.fakes["s3"].put_csv_gz(REPORTS_BUCKET, data, rows)
        manifest = f"{PREFIX}/metadata/BILLING_PERIOD={period}/account-serverless-Manifest.json"
        body = {"dataFiles": [f"s3://{REPORTS_BUCKET}/{data}"]} if listed else {}
        self.fakes["s3"].put(REPORTS_BUCKET, manifest, json.dumps(body).encode("utf-8"))
        # EventBridge gives the key URL-encoded.
        encoded = manifest.replace("=", "%3D")
        return app.handler({"source": "aws.s3", "detail": {"bucket": {"name": REPORTS_BUCKET}, "object": {"key": encoded}}}, None)

    def test_report_sums(self):
        result = self.report("2026-10", [
            self.row("2026-10-01", "AmazonS3", 0.25, tags={"user_Project": "wiki"}),
            self.row("2026-10-01", "AWSLambda", 0.5, tags={"user_project": "search"}),
            self.row("2026-10-02", "AmazonS3", 0.25),
            self.row("2026-10-01", "AmazonS3", 0.1, kind="Tax"),
        ])
        self.assertEqual((result["period"], result["files"], result["rows"]), ("2026-10", 1, 4))
        month = common.plain(self.table.items[("COST", "2026-10")])
        self.assertAlmostEqual(month["total"], 1.1)
        self.assertEqual(month["usage_days"], {"2026-10-01": 0.75, "2026-10-02": 0.25})
        self.assertEqual(month["kinds"], {"Usage": 1.0, "Tax": 0.1})
        self.assertEqual(month["projects"], {"wiki": 0.25, "search": 0.5, "(untagged)": 0.35})
        self.assertEqual(month["day_services"]["2026-10-01"], {"AmazonS3": 0.25, "AWSLambda": 0.5})

    def test_manifest_without_files(self):
        self.report("2026-09", [self.row("2026-09-03", "AmazonS3", 1)], listed=False)
        self.assertEqual(common.plain(self.table.items[("COST", "2026-09")]["total"]), 1)

    def test_budgets_spike_and_new_service(self):
        self.fakes["budgets"].budgets = [{
            "BudgetName": "Monthly", "BudgetType": "COST", "TimeUnit": "MONTHLY", "BudgetLimit": {"Amount": "4", "Unit": "USD"},
            "CalculatedSpend": {"ActualSpend": {"Amount": "2.5", "Unit": "USD"}, "ForecastedSpend": {"Amount": "6", "Unit": "USD"}},
        }]
        costs.refresh_budgets()
        self.report("2026-09", [self.row("2026-09-10", "AmazonS3", 0.1)])
        rows = [self.row(f"2026-10-{day:02d}", "AmazonS3", 0.1) for day in range(1, 16)]
        rows.append(self.row("2026-10-16", "AmazonEC2", 3))
        self.report("2026-10", rows)
        found = sorted(item["rule"] for item in costs.check("2026-10-18"))
        self.assertEqual(found, ["budget_forecast", "cost_spike", "new_service"])
        self.assertEqual(costs.check("2026-10-18"), [])
        spike = next(item for item in self.findings() if item["rule"] == "cost_spike")
        self.assertEqual(common.plain(spike["details"])["services"], {"AmazonEC2": 3})


class MailTest(Base):
    def test_digest_language_prefs_and_once(self):
        self.fakes["cognito-idp"].add("ru@example.com", locale="ru")
        self.fakes["cognito-idp"].add("off@example.com")
        self.fakes["cognito-idp"].add("gone@example.com", enabled=False)
        self.table.put_item(Item={"pk": "USER#off@example.com", "sk": "PREFS", "digest": False, "alerts": True})
        self.table.put_item(Item=common.clean_item({"pk": "COST", "sk": "2026-10", "total": 1.5, "currency": "USD",
                                                    "usage_days": {"2026-10-16": 0.2}, "services": {"AmazonS3": 1.5}}))
        self.table.put_item(Item={"pk": "TRAIL", "sk": "DAY#2026-10-17", "events": 10, "writes": 2, "errors": 1, "signins": 1,
                                  "principals": {"root": 1}})
        self.assertEqual(mail.send_digest("2026-10-17"), 2)
        sent = {item["to"][0]: item for item in self.fakes["sesv2"].sent}
        self.assertEqual(set(sent), {OWNER, "ru@example.com"})
        self.assertTrue(sent[OWNER]["subject"].startswith("AWS on 2026-10-17: $1.50"))
        self.assertTrue(sent["ru@example.com"]["subject"].startswith("AWS за 2026-10-17"))
        self.assertIn("10 calls, 2 changes", sent[OWNER]["text"])
        self.assertEqual(mail.send_digest("2026-10-17"), 0)

    def test_no_sender_no_mail(self):
        os.environ["SENDER"] = ""
        self.assertEqual(mail.send_digest("2026-10-17"), 0)
        self.assertEqual(mail.send_alerts([{"rule": "root_activity", "severity": "critical"}]), 0)


class SsoTest(Base):
    def setUp(self):
        super().setUp()
        os.environ.update({"SSO_PROVIDER": "IdentityCenter", "IDENTITY_STORE_ID": "d-1", "SSO_ADMIN_GROUPS": "FullAccess"})
        self.store = self.fakes["identitystore"]
        self.store.add("boss@example.com", "FullAccess", "ReadAll")
        self.store.add("viewer@example.com", "ReadAll")
        import sso
        sso._group_names.clear()

    def token(self, email, groups=(), provider="IdentityCenter"):
        identities = json.dumps([{"providerName": provider, "providerType": "SAML"}]) if provider else None
        attributes = {"email": email, **({"identities": identities} if identities else {})}
        event = {"triggerSource": "TokenGeneration_HostedAuth", "request": {
            "userAttributes": attributes, "groupConfiguration": {"groupsToOverride": list(groups)}}, "response": {}}
        return app.handler(event, None)["response"].get("claimsOverrideDetails")

    def test_token(self):
        boss = self.token("boss@example.com")
        self.assertEqual(boss, {"claimsToAddOrOverride": {"email": "boss@example.com", "email_verified": "true"},
                                "groupOverrideDetails": {"groupsToOverride": ["admins"]}})
        # Identity Center decides: an administrator in the pool is not one
        # unless their group says so.
        viewer = self.token("viewer@example.com", groups=["admins"])
        self.assertEqual(viewer["groupOverrideDetails"]["groupsToOverride"], [])
        self.assertEqual(self.token("stranger@example.com")["groupOverrideDetails"]["groupsToOverride"], [])
        # A user with a password is left alone.
        self.assertIsNone(self.token("owner@example.com", groups=["admins"], provider=None))

    def test_token_with_the_address_as_subject(self):
        # Without the SAML attribute email the address is only the subject.
        identities = json.dumps([{"userId": "Boss@Example.com", "providerName": "IdentityCenter", "providerType": "SAML"}])
        event = {"triggerSource": "TokenGeneration_HostedAuth", "request": {
            "userAttributes": {"identities": identities}, "groupConfiguration": {"groupsToOverride": []}}, "response": {}}
        claims = app.handler(event, None)["response"]["claimsOverrideDetails"]
        self.assertEqual(claims["claimsToAddOrOverride"], {"email": "boss@example.com", "email_verified": "true"})
        self.assertEqual(claims["groupOverrideDetails"]["groupsToOverride"], ["admins"])
        user = {"Username": "IdentityCenter_boss@example.com", "Attributes": [{"Name": "identities", "Value": identities}]}
        self.assertEqual(app.user_email(user), "boss@example.com")

    def test_token_without_identity_center(self):
        def broken(**kwargs):
            raise fakes.ClientError("ThrottlingException")
        self.store.get_user_id = broken
        claims = self.token("boss@example.com", groups=["admins"])
        self.assertEqual(claims["claimsToAddOrOverride"], {"email": "boss@example.com", "email_verified": "true"})
        self.assertEqual(claims["groupOverrideDetails"]["groupsToOverride"], [])

    def test_users_of_sso(self):
        cognito = self.fakes["cognito-idp"]
        cognito.add("boss@example.com", sso=True)
        cognito.add(OWNER, sso=True)
        status, data = self.call("GET", "/api/admin/users", admin=True)
        rows = {(user["email"], user["sso"]): user for user in data["items"]}
        self.assertTrue(rows[("boss@example.com", True)]["admin"])
        self.assertFalse(rows[(OWNER, False)]["admin"])
        boss = {"email": "boss@example.com", "username": "IdentityCenter_boss@example.com"}
        self.assertEqual(self.call("PUT", "/api/admin/users", {**boss, "admin": False}, admin=True)[1]["code"], "sso_user")
        self.assertEqual(self.call("PUT", "/api/admin/users", {**boss, "enabled": False}, admin=True)[0], 200)
        self.assertFalse(cognito.users["IdentityCenter_boss@example.com"]["Enabled"])
        wrong = {"email": "boss@example.com", "username": OWNER, "enabled": False}
        self.assertEqual(self.call("PUT", "/api/admin/users", wrong, admin=True)[1]["code"], "user_not_found")
        # Two users, one email: one mail.
        self.assertEqual([email for email, _ in mail.recipients("digest")].count(OWNER), 1)
        # The SSO user of the same email goes; the mails of the email stay.
        self.table.put_item(Item={"pk": "USER#" + OWNER, "sk": "PREFS", "digest": False, "alerts": True})
        app.delete_user(OWNER, "IdentityCenter_" + OWNER)
        self.assertIn(("USER#" + OWNER, "PREFS"), self.table.items)


class ApiTest(Base):
    def test_me_and_prefs(self):
        status, me = self.call("GET", "/api/me")
        self.assertEqual((status, me["admin"], me["prefs"]), (200, False, {"digest": True, "alerts": True}))
        self.assertIn("root_activity", [rule["id"] for rule in me["rules"]])
        status, prefs = self.call("PUT", "/api/me/prefs", {"digest": False})
        self.assertEqual((status, prefs), (200, {"digest": False, "alerts": True}))
        self.assertEqual(self.call("PUT", "/api/me/prefs", {"digest": "no"})[1]["code"], "bad_flag")

    def test_events_findings_and_admin(self):
        trail.process([record("CreateAccessKey", time=f"2026-10-17T10:{minute:02d}:00Z") for minute in range(3)])
        status, data = self.call("GET", "/api/trail/events", params={"day": "2026-10-17"})
        self.assertEqual((status, len(data["items"]), data["items"][0]["time"]), (200, 3, "2026-10-17T10:02:00Z"))
        status, event = self.call("GET", "/api/trail/events/2026-10-17/" + data["items"][0]["sk"])
        self.assertEqual((status, event["name"]), (200, "CreateAccessKey"))
        status, found = self.call("GET", "/api/findings")
        self.assertEqual(len(found["items"]), 3)
        finding = found["items"][0]
        self.assertEqual(self.call("PATCH", "/api/findings", {"sk": finding["sk"], "ack": True})[0], 403)
        status, done = self.call("PATCH", "/api/findings", {"sk": finding["sk"], "ack": True}, admin=True)
        self.assertEqual((status, done["ack"], done["ack_by"]), (200, True, OWNER))
        status, days = self.call("GET", "/api/trail/days", params={"from": "2026-10-10", "to": "2026-10-17"})
        self.assertEqual(days["items"][0]["events"], 3)
        self.assertEqual(self.call("GET", "/api/trail/days", params={"from": "2026-01-01", "to": "2026-10-17"})[1]["code"], "bad_range")

    def test_events_by_field(self):
        bob = {"type": "IAMUser", "userName": "bob"}
        records = [record("TagRole", time=f"2026-10-17T09:{minute:02d}:00Z") for minute in range(5)]
        records.append(record("PutObject", "s3", who=bob, time="2026-10-17T08:00:00Z", errorCode="AccessDenied"))
        trail.process(records)
        status, data = self.call("GET", "/api/trail/events", params={"day": "2026-10-17", "principal": "user/bob"})
        self.assertEqual((status, [item["name"] for item in data["items"]]), (200, ["PutObject"]))
        both = {"day": "2026-10-17", "source": "iam", "name": "TagRole"}
        self.assertEqual(len(self.call("GET", "/api/trail/events", params=both)[1]["items"]), 5)
        self.assertEqual(self.call("GET", "/api/trail/events", params={**both, "errors": "1"})[1]["items"], [])
        # A narrow filter reads on past a page that has nothing for it.
        app.EVENTS_PAGE = 2
        try:
            status, data = self.call("GET", "/api/trail/events", params={"day": "2026-10-17", "error": "AccessDenied"})
            self.assertEqual([item["principal"] for item in data["items"]], ["user/bob"])
        finally:
            app.EVENTS_PAGE = 200

    def test_summary_by_several_filters(self):
        bob = {"type": "IAMUser", "userName": "bob"}
        trail.process([
            record("Encrypt", "kms", who=bob, time="2026-10-16T09:00:00Z"),
            record("Decrypt", "kms", who=bob, time="2026-10-17T09:00:00Z", readOnly=True),
            record("Decrypt", "kms", who=bob, time="2026-10-17T09:01:00Z", readOnly=True),
            record("CreateKey", "kms", who=bob, time="2026-10-17T09:02:00Z"),
            record("CreateKey", "kms", time="2026-10-17T09:05:00Z"),
            record("TagRole", who=bob, time="2026-10-17T09:10:00Z", errorCode="AccessDenied"),
        ], "file-1")
        base = {"from": "2026-10-15", "to": "2026-10-17", "day": "2026-10-17"}

        def summary(**params):
            status, data = self.call("GET", "/api/trail/summary", params={**base, **params})
            self.assertEqual(status, 200)
            return data
        both = summary(principal="user/bob", source="kms")
        # The reads count too: two Decrypt and one CreateKey that day.
        self.assertEqual(both["days"], {"2026-10-15": None, "2026-10-16": {"events": 1, "writes": 1, "errors": 0, "signins": 0},
                                        "2026-10-17": {"events": 3, "writes": 1, "errors": 0, "signins": 0}})
        self.assertEqual(both["day"]["changes"], {"kms:CreateKey": 1})
        self.assertEqual(summary(principal="user/bob", source="kms", writes="1")["day"]["events"], 1)
        bob_day = summary(principal="user/bob")["day"]
        self.assertEqual((bob_day["events"], bob_day["errors"], bob_day["sources"]), (4, 1, {"kms": 3, "iam": 1}))
        # Reads keep no event name: a text finds the changes by theirs.
        self.assertEqual(summary(q="CREATEKEY")["day"]["events"], 2)
        self.assertEqual(summary(q="decrypt")["day"]["events"], 0)
        # Folded into the day, the counts stay the same, once.
        self.assertEqual(combos.compact_recent("2026-10-18"), 2)
        self.assertEqual(combos.compact_recent("2026-10-18"), 0)
        self.assertEqual(summary(principal="user/bob", source="kms")["days"], both["days"])
        # The events take the text too.
        status, data = self.call("GET", "/api/trail/events", params={"day": "2026-10-17", "q": "createkey"})
        self.assertEqual(len(data["items"]), 2)

    def test_settings(self):
        self.assertEqual(self.call("PUT", "/api/admin/settings", {"muted": []})[0], 403)
        status, saved = self.call("PUT", "/api/admin/settings", {"muted": ["iam_change"], "regions": ["eu-west-1"],
                                                                 "ignored": ["role/AWSReservedSSO_*"]}, admin=True)
        self.assertEqual((status, saved["muted"], saved["regions"]), (200, ["iam_change"], ["eu-west-1"]))
        self.assertEqual(self.call("PUT", "/api/admin/settings", {"muted": ["nope"]}, admin=True)[1]["code"], "bad_rule")
        self.assertEqual(self.call("PUT", "/api/admin/settings", {"regions": ["Mars"]}, admin=True)[1]["code"], "bad_region")
        self.assertEqual(self.call("GET", "/api/rules")[1]["settings"]["ignored"], ["role/AWSReservedSSO_*"])

    def test_overview_costs_and_refresh(self):
        period = common.now_utc().strftime("%Y-%m")
        self.table.put_item(Item=common.clean_item({"pk": "COST", "sk": period, "period": period, "total": 2.0}))
        status, data = self.call("GET", "/api/overview")
        self.assertEqual((status, data["month"]["total"], data["findings"]["critical"]), (200, 2, 0))
        self.assertEqual(self.call("GET", "/api/costs")[1]["periods"], [period])
        self.assertEqual(self.call("GET", "/api/costs/2026-1")[1]["code"], "bad_period")
        self.assertEqual(self.call("POST", "/api/admin/refresh", admin=True)[0], 202)
        self.assertEqual(self.fakes["lambda"].calls, [{"action": "refresh"}])

    def test_users(self):
        status, created = self.call("POST", "/api/admin/users", {"email": "new@example.com", "admin": True}, admin=True)
        self.assertEqual((status, created["email"]), (201, "new@example.com"))
        status, data = self.call("GET", "/api/admin/users", admin=True)
        self.assertEqual([user["email"] for user in data["items"]], ["new@example.com", OWNER])
        self.assertEqual(self.call("DELETE", "/api/admin/users", params={"email": OWNER}, admin=True)[1]["code"], "not_yourself")


class BackfillTest(Base):
    DAY = "2026-10-17"
    BASE = "AWSLogs/1/CloudTrail/"

    def log(self, key, records):
        self.fakes["s3"].put_json_gz(TRAIL_BUCKET, self.BASE + key, {"Records": records})

    def start(self, **body):
        payload = {"from": self.DAY, "to": self.DAY, "keep_days": 30, "trail": True, "costs": False, **body}
        return self.call("POST", "/api/admin/backfill", payload, admin=True)

    def finish(self, job, context=None):
        for _ in range(50):
            result = app.handler({"action": "backfill", "job": job["sk"]}, context)
            if result["status"] != "running":
                return result
        self.fail("the job did not finish")

    def test_day_replaces_what_the_live_loading_wrote(self):
        live = [record("CreateAccessKey", time=self.DAY + "T10:00:00Z"), record("ListBuckets", "s3", readOnly=True, time=self.DAY + "T10:01:00Z")]
        first = "eu-central-1/2026/10/17/1_CloudTrail_eu-central-1_20261017T1005Z_a.json.gz"
        trail.process(live, combos.file_id(self.BASE + first))
        self.log(first, live)
        self.log("us-east-1/2026/10/17/1_CloudTrail_us-east-1_20261017T2300Z_b.json.gz",
                 [record("GetUser", readOnly=True, time=self.DAY + "T22:58:00Z")])
        # The next day's folder: the last minutes of the day, in its first hour only.
        self.log("us-east-1/2026/10/18/1_CloudTrail_us-east-1_20261018T0005Z_c.json.gz",
                 [record("PutObject", "s3", errorCode="AccessDenied", time=self.DAY + "T23:59:00Z"),
                  record("ListUsers", readOnly=True, time="2026-10-18T00:01:00Z")])
        self.log("us-east-1/2026/10/18/1_CloudTrail_us-east-1_20261018T0130Z_d.json.gz",
                 [record("DeleteUser", time=self.DAY + "T23:59:30Z")])
        findings, mails = len(self.findings()), len(self.fakes["sesv2"].sent)
        status, job = self.start()
        self.assertEqual((status, job["status"]), (202, "running"))
        self.assertEqual(self.fakes["lambda"].calls[-1], {"action": "backfill", "job": job["sk"]})
        self.assertEqual(self.finish(job)["status"], "done")
        day = self.table.items[("TRAIL", "DAY#" + self.DAY)]
        self.assertEqual((day["events"], day["writes"], day["errors"]), (4, 2, 1))
        # The live loading wrote the day for good: it stays so.
        self.assertNotIn("expires", day)
        folded = self.table.items[("COMBOS#" + self.DAY, "DAY")]
        self.assertEqual(sum(folded["n"].values()), 4)
        self.assertEqual(len(folded["files"]), 3)
        self.assertEqual(combos.read(self.DAY).total(), 4)
        events = [item for (pk, _), item in self.table.items.items() if pk == "EVENTS#" + self.DAY]
        self.assertEqual(sorted(item["name"] for item in events), ["CreateAccessKey", "PutObject"])
        self.assertTrue(all(item["expires"] >= backfill.event_expiry({"eventTime": item["time"]}) for item in events))
        self.assertEqual((len(self.findings()), len(self.fakes["sesv2"].sent)), (findings, mails))
        status, data = self.call("GET", "/api/admin/backfill", admin=True)
        self.assertEqual((data["items"][0]["status"], data["items"][0]["days"], data["items"][0]["files"]), ("done", 1, 3))
        # Read again: the same numbers.
        self.finish(self.start()[1])
        self.assertEqual(self.table.items[("TRAIL", "DAY#" + self.DAY)]["events"], 4)

    def test_old_days_expire_and_months_are_read(self):
        self.log("eu-central-1/2026/03/01/1_CloudTrail_eu-central-1_20260301T1000Z_a.json.gz",
                 [record("ListBuckets", "s3", readOnly=True, time="2026-03-01T09:59:00Z")])
        data = f"{PREFIX}/data/BILLING_PERIOD=2026-03/account-serverless-00001.csv.gz"
        self.fakes["s3"].put_csv_gz(REPORTS_BUCKET, data, [{"line_item_unblended_cost": "1.5", "line_item_usage_start_date": "2026-03-01T00:00:00Z",
                                                            "line_item_product_code": "AmazonS3", "line_item_line_item_type": "Usage"}])
        self.fakes["s3"].put(REPORTS_BUCKET, f"{PREFIX}/metadata/BILLING_PERIOD=2026-03/x-Manifest.json", b"{}")

        class Context:
            def get_remaining_time_in_millis(self):
                return 600_000

        status, job = self.start(**{"from": "2026-02-27", "to": "2026-03-02", "keep_days": 400, "costs": True})
        self.assertEqual(self.finish(job, Context())["status"], "done")
        done = common.plain(self.table.items[("BACKFILL", job["sk"])])
        self.assertEqual((done["days"], done["months"], done["records"]), (4, 2, 1))
        until = done["keep_until"]
        self.assertEqual(self.table.items[("TRAIL", "DAY#2026-03-01")]["expires"], until)
        self.assertEqual(self.table.items[("COMBOS#2026-03-01", "DAY")]["expires"], until)
        self.assertEqual(self.table.items[("COST", "2026-03")]["expires"], until)
        self.assertNotIn(("TRAIL", "DAY#2026-02-28"), self.table.items)

    def test_checks_one_at_a_time_and_stop(self):
        yesterday = common.days_before(common.day_of(common.now_utc()), 1)
        self.assertEqual(self.start(to=common.day_of(common.now_utc()))[1]["code"], "bad_backfill_range")
        self.assertEqual(self.start(**{"from": "2023-01-01", "to": yesterday})[1]["code"], "bad_backfill_range")
        self.assertEqual(self.start(keep_days=0)[1]["code"], "bad_keep")
        self.assertEqual(self.start(trail=False)[1]["code"], "bad_backfill_kinds")
        self.assertEqual(self.call("POST", "/api/admin/backfill", {}, admin=False)[1]["code"], "admin_only")
        status, job = self.start()
        self.assertEqual(self.start()[0], 409)
        status, stopped = self.call("DELETE", "/api/admin/backfill", params={"id": job["sk"]}, admin=True)
        self.assertEqual((status, stopped["status"]), (200, "stopped"))
        self.assertEqual(self.finish(job)["status"], "stopped")
        self.assertEqual(self.start()[0], 202)
        self.assertEqual(self.call("DELETE", "/api/admin/backfill", params={"id": "nope"}, admin=True)[0], 404)


if __name__ == "__main__":
    unittest.main()
