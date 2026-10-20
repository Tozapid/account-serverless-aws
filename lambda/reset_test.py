import json
import os
import unittest
from datetime import timedelta

import app
import common
import fakes
import reset

DEMO = "demo@example.com"


class ResetTest(unittest.TestCase):
    def setUp(self):
        self.fakes = fakes.install()
        os.environ.update({"TABLE_NAME": "account", "USER_POOL_ID": "pool", "ADMIN_GROUP": "admins", "DEMO_EMAIL": DEMO,
                           "DEMO_PASSWORD_PARAMETER": "/account-serverless/admin-password", "COST_TAG": "Project",
                           "SPIKE_FACTOR": "2", "SPIKE_MIN_USD": "0.5", "ALLOWED_REGIONS": "eu-central-1,us-east-1",
                           "EVENT_DAYS": "90", "SENDER": "", "ACCOUNT_ID": "123456789012"})
        self.fakes["ssm"].values["/account-serverless/admin-password"] = "Demo-Password-1"
        cognito = self.fakes["cognito-idp"]
        cognito.add(DEMO, locale="fr")
        cognito.add("visitor@example.com")
        self.seed = json.loads(reset.SEED.read_text(encoding="utf-8"))

    def call(self, method, path, body=None, params=None):
        result = app.handler(fakes.event(method, path, body, params, email=DEMO, admin=True), None)
        return result["statusCode"], json.loads(result["body"])

    def rules_found(self):
        return {item["rule"] for item in common.query_all("FINDINGS")}

    def test_full_reset_through_the_api(self):
        table = self.fakes["table"]
        table.put_item(Item={"pk": "USER#visitor@example.com", "sk": "PREFS", "digest": False})
        result = reset.handler({"reason": "deploy"}, None)
        self.assertTrue(result["full"])
        self.assertGreater(result["records"], 1000)
        self.assertNotIn(("USER#visitor@example.com", "PREFS"), table.items)
        # Costs: the months of the seed, the budgets and the checks of the daily job.
        _, months = self.call("GET", "/api/costs")
        self.assertEqual(len(months["periods"]), self.seed["costs"]["months"])
        _, overview = self.call("GET", "/api/overview")
        self.assertGreater(overview["month"]["total"], 0)
        self.assertEqual([budget["name"] for budget in overview["budgets"]["budgets"]], ["monthly-total", "build-ec2"])
        self.assertEqual(len(overview["trail"]), 14)
        # CloudTrail through the code of the logs: counters, events, filters, findings.
        today = common.day_of(common.now_utc())
        week_ago = common.days_before(today, 6)
        _, days = self.call("GET", "/api/trail/days", params={"from": week_ago, "to": today})
        self.assertTrue(all(day["events"] > 0 for day in days["items"]))
        two_days_ago = common.days_before(today, 2)
        _, events = self.call("GET", "/api/trail/events", params={"day": two_days_ago, "principal": "user/contractor"})
        self.assertIn("PutBucketPolicy", {event["name"] for event in events["items"]})
        _, summary = self.call("GET", "/api/trail/summary", params={"from": week_ago, "to": today, "principal": "role/Terraform/github-actions"})
        self.assertTrue(any(day and day["events"] for day in summary["days"].values()))
        found = self.rules_found()
        for rule in ("root_activity", "trail_tampering", "iam_credentials", "open_port", "public_access", "unusual_region",
                     "console_login_failed", "console_login_no_mfa", "new_principal", "new_login_ip", "access_denied",
                     "cost_spike", "new_service"):
            self.assertIn(rule, found)
        for rule in ("insight", "savings"):
            self.assertIn(rule, found)
        # Recommendations, by savings, and the traffic of the sources of the seed.
        _, recs = self.call("GET", "/api/recommendations")
        self.assertEqual(recs["count"], len(self.seed["recommendations"]["rows"]))
        self.assertEqual(recs["items"][0]["resource"], "i-0a1b2c3d4e5f60718")
        _, sources = self.call("GET", "/api/traffic")
        self.assertEqual({item["source"] for item in sources["sources"]}, {item["source"] for item in self.seed["traffic"]["sources"]})
        yesterday = common.days_before(today, 1)
        _, site = self.call("GET", "/api/traffic/days", params={"source": "cloudfront/website", "from": common.days_before(today, 13), "to": today})
        self.assertTrue(all(site["days"][day] and site["days"][day]["requests"] > 0 for day in site["days"] if day != today))
        self.assertIsNotNone(common.get("TRAFFIC#cloudfront/website#" + yesterday, "DAY"))
        _, bucket = self.call("GET", "/api/traffic/day", params={"source": "s3/orders-uploads-123456789012", "day": yesterday})
        self.assertIn("role/orders-api/orders-api", bucket["counts"]["requesters"])
        # Only the incidents are news: the routine was learned first.
        principals = {item.get("principal") for item in common.query_all("FINDINGS") if item["rule"] == "new_principal"}
        self.assertEqual(principals, {"user/contractor"})
        acknowledged = {item["rule"] for item in common.query_all("FINDINGS") if item.get("ack")}
        self.assertEqual(acknowledged, set(self.seed["trail"]["acknowledged"]))
        # The demo user only, as deployed.
        cognito = self.fakes["cognito-idp"]
        self.assertEqual(list(cognito.users), [DEMO])
        user = cognito.users[DEMO]
        self.assertEqual((user["password"], user["mfa"], user["signed_out"]), ("Demo-Password-1", False, True))
        self.assertNotIn("locale", [item["Name"] for item in user["Attributes"]])
        self.assertIn(DEMO, cognito.groups["admins"])

    def test_light_reset_puts_back_what_visitors_change(self):
        reset.handler({"reason": "deploy"}, None)
        events = len(self.fakes["table"].items)
        self.call("PUT", "/api/admin/settings", {"muted": ["iam_change"]})
        self.call("PUT", "/api/me/prefs", {"digest": False})
        finding = next(item for item in common.query_all("FINDINGS") if not item.get("ack"))
        self.call("PATCH", "/api/findings", {"sk": finding["sk"], "ack": True})
        # An hour later: the records of the hour are added, nothing is made twice.
        state = common.get("SEED", "STATE")
        earlier = common.iso(common.now_utc() - timedelta(hours=1))
        self.fakes["table"].put_item(Item={**state, "until": earlier})
        result = reset.handler({}, None)
        self.assertFalse(result["full"])
        self.assertGreaterEqual(result["restored"], 3)
        self.assertIsNone(common.get("SETTINGS", "RULES"))
        self.assertIsNone(common.get("USER#" + DEMO, "PREFS"))
        self.assertFalse(common.get("FINDINGS", finding["sk"]).get("ack"))
        self.assertGreaterEqual(len(self.fakes["table"].items), events - 2)
        self.assertGreater(result["requests"], 0)
        # The same hour again adds nothing.
        again = reset.handler({}, None)
        self.assertEqual((again["records"], again["requests"]), (0, 0))

    def test_demo_is_read_only_where_it_matters(self):
        os.environ["DEMO"] = "true"
        try:
            self.assertEqual(self.call("GET", "/api/admin/users")[0], 200)
            for method, path, body in (("POST", "/api/admin/users", {"email": "new@example.com"}), ("POST", "/api/admin/refresh", None),
                                       ("POST", "/api/admin/backfill", {"from": "2026-01-01", "to": "2026-01-02", "keep_days": 1, "trail": True, "costs": False})):
                status, data = self.call(method, path, body)
                self.assertEqual((status, data["code"]), (403, "demo_readonly"))
            self.assertTrue(self.call("GET", "/api/me")[1]["demo"])
            self.assertEqual(self.call("PUT", "/api/admin/settings", {"muted": []})[0], 200)
        finally:
            os.environ.pop("DEMO")


if __name__ == "__main__":
    unittest.main()
