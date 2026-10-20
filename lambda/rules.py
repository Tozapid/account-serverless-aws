"""The rules: what a CloudTrail record or the costs are checked for.

A rule has an id, a severity and a source. "critical" is mailed at once to
the users who want alerts; "warning" goes into the daily digest. Each rule of
CloudTrail looks at one record (check); those that need more than one record
(new principals, new sign-in addresses, bursts of denied calls, CloudTrail
Insights) and the rules of the costs live in trail.py, costs.py and
recommendations.py, with their ids listed here so that
the page and the settings know every rule.

A record that failed (errorCode) changed nothing, so the rules of changes skip
it; a failed sign-in is a finding of its own. Records of AWS services acting on
their own (AWSService, AwsServiceEvent) are not checked.
"""

import fnmatch
import json

CRITICAL = "critical"
WARNING = "warning"

RULES = {
    "root_activity": (CRITICAL, "trail"),
    "console_login_no_mfa": (CRITICAL, "trail"),
    "trail_tampering": (CRITICAL, "trail"),
    "security_off": (CRITICAL, "trail"),
    "iam_credentials": (CRITICAL, "trail"),
    "iam_admin": (CRITICAL, "trail"),
    "public_access": (CRITICAL, "trail"),
    "kms_deletion": (CRITICAL, "trail"),
    "console_login_failed": (WARNING, "trail"),
    "open_port": (WARNING, "trail"),
    "iam_change": (WARNING, "trail"),
    "account_change": (WARNING, "trail"),
    "unusual_region": (WARNING, "trail"),
    "new_principal": (WARNING, "trail"),
    "new_login_ip": (WARNING, "trail"),
    "access_denied": (WARNING, "trail"),
    "insight": (WARNING, "trail"),
    "budget_exceeded": (CRITICAL, "cost"),
    "budget_forecast": (WARNING, "cost"),
    "cost_spike": (WARNING, "cost"),
    "new_service": (WARNING, "cost"),
    "savings": (WARNING, "cost"),
}

TRAIL_CHANGES = {"StopLogging", "DeleteTrail", "UpdateTrail", "PutEventSelectors", "PutInsightSelectors",
                 "DeleteEventDataStore", "StopEventDataStoreIngestion"}
SECURITY_OFF = {
    ("guardduty", "DeleteDetector"), ("guardduty", "DisassociateFromMasterAccount"),
    ("guardduty", "DisassociateFromAdministratorAccount"), ("config", "StopConfigurationRecorder"),
    ("config", "DeleteConfigurationRecorder"), ("config", "DeleteDeliveryChannel"),
    ("securityhub", "DisableSecurityHub"), ("access-analyzer", "DeleteAnalyzer"),
}
IAM_CREDENTIALS = {"CreateAccessKey", "CreateUser", "CreateLoginProfile", "UpdateLoginProfile",
                   "DeactivateMFADevice", "DeleteVirtualMFADevice", "UploadSSHPublicKey",
                   "CreateServiceSpecificCredential"}
ADMIN_POLICIES = ("/AdministratorAccess", "/IAMFullAccess")
ACCOUNT_SOURCES = {"organizations", "account", "billing", "billingconsole", "payments", "tax", "invoicing"}
DENIED = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "Client.UnauthorizedOperation"}
# Ports a security group may open to everyone without a finding.
WEB_PORTS = {80, 443}


# What a record is ----------------------------------------------------------------------

def principal(record):
    """Who: root, an IAM user, role/session of an assumed role, a service."""
    identity = record.get("userIdentity") or {}
    kind = identity.get("type", "")
    if kind == "Root":
        return "root"
    if kind == "IAMUser":
        return "user/" + identity.get("userName", "?")
    if kind == "AssumedRole":
        issuer = ((identity.get("sessionContext") or {}).get("sessionIssuer") or {}).get("userName", "")
        session = (identity.get("arn") or "").rsplit("/", 1)[-1]
        return f"role/{issuer or '?'}/{session}"
    if kind == "AWSService":
        return "service/" + (identity.get("invokedBy") or record.get("sourceIPAddress") or "?")
    if kind == "IdentityCenterUser":
        return "sso/" + (identity.get("onBehalfOf") or {}).get("userId", identity.get("userName", "?"))
    return (kind or "unknown") + ("/" + identity["userName"] if identity.get("userName") else "")


def service(record):
    return (record.get("eventSource") or "").replace(".amazonaws.com", "")


def signin(record):
    return record.get("eventName") == "ConsoleLogin"


def write(record):
    """A call that may change something. A record without readOnly counts,
    except a sign-in, which is counted as one."""
    return record.get("readOnly") is not True and not signin(record)


def by_aws(record):
    identity = record.get("userIdentity") or {}
    return identity.get("type") == "AWSService" or record.get("eventType") == "AwsServiceEvent"


def denied(record):
    return record.get("errorCode") in DENIED


def ignored(name, patterns):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


# Rules of one record ------------------------------------------------------------------

def check(record, regions):
    """The rules one record breaks, by id."""
    if by_aws(record):
        return []
    found = []
    identity = record.get("userIdentity") or {}
    source, name = service(record), record.get("eventName", "")
    if identity.get("type") == "Root" and not identity.get("invokedBy"):
        found.append("root_activity")
    if signin(record):
        result = (record.get("responseElements") or {}).get("ConsoleLogin")
        mfa = (record.get("additionalEventData") or {}).get("MFAUsed")
        if result == "Failure":
            found.append("console_login_failed")
        elif result == "Success" and mfa == "No" and identity.get("type") in ("IAMUser", "Root"):
            found.append("console_login_no_mfa")
        return found
    if record.get("errorCode") or not write(record):
        return found
    params = record.get("requestParameters") or {}
    if source == "cloudtrail" and name in TRAIL_CHANGES:
        found.append("trail_tampering")
    if (source, name) in SECURITY_OFF or (source == "guardduty" and name == "UpdateDetector" and params.get("enable") is False):
        found.append("security_off")
    if source == "iam":
        if name in IAM_CREDENTIALS or (name == "UpdateAccessKey" and params.get("status") == "Active"):
            found.append("iam_credentials")
        elif admin_policy(name, params):
            found.append("iam_admin")
        else:
            found.append("iam_change")
    if public(source, name, params):
        found.append("public_access")
    if source == "kms" and name in ("ScheduleKeyDeletion", "DisableKey"):
        found.append("kms_deletion")
    if source == "ec2" and name == "AuthorizeSecurityGroupIngress" and open_to_all(params):
        found.append("open_port")
    if source in ACCOUNT_SOURCES:
        found.append("account_change")
    region = record.get("awsRegion", "")
    if regions and region and region not in regions:
        found.append("unusual_region")
    return found


def admin_policy(name, params):
    if name in ("AttachUserPolicy", "AttachGroupPolicy", "AttachRolePolicy"):
        return str(params.get("policyArn", "")).endswith(ADMIN_POLICIES)
    if name in ("PutUserPolicy", "PutGroupPolicy", "PutRolePolicy"):
        return any(allows_everything(statement) for statement in statements(params.get("policyDocument")))
    return False


def statements(document):
    if isinstance(document, str):
        try:
            from urllib.parse import unquote
            document = json.loads(unquote(document))
        except ValueError:
            return []
    if not isinstance(document, dict):
        return []
    found = document.get("Statement") or []
    return found if isinstance(found, list) else [found]


def as_list(value):
    return value if isinstance(value, list) else [value]


def allows_everything(statement):
    return (isinstance(statement, dict) and statement.get("Effect") == "Allow"
            and "*" in as_list(statement.get("Action")) and "*" in as_list(statement.get("Resource")))


def public(source, name, params):
    """A bucket, snapshot or image opened to everyone, or the block of
    public access taken off."""
    if source == "s3" and name == "DeleteBucketPublicAccessBlock":
        return True
    if source == "s3control" and name == "DeletePublicAccessBlock":
        return True
    if name == "PutBucketPublicAccessBlock" or (source == "s3control" and name == "PutPublicAccessBlock"):
        config = params.get("PublicAccessBlockConfiguration") or {}
        return any(config.get(flag) is False for flag in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"))
    if source == "s3" and name == "PutBucketPolicy":
        for statement in statements(params.get("bucketPolicy")):
            who = statement.get("Principal") if isinstance(statement, dict) else None
            everyone = who == "*" or (isinstance(who, dict) and "*" in as_list(who.get("AWS")))
            if everyone and statement.get("Effect") == "Allow" and not statement.get("Condition"):
                return True
        return False
    if source == "s3" and name == "PutBucketAcl":
        text = json.dumps(params)
        return "global/AllUsers" in text or "global/AuthenticatedUsers" in text
    if source == "ec2" and name in ("ModifySnapshotAttribute", "ModifyImageAttribute"):
        return '"group": "all"' in json.dumps(params)
    if source == "rds" and name in ("ModifyDBSnapshotAttribute", "ModifyDBClusterSnapshotAttribute"):
        return "all" in as_list(params.get("valuesToAdd") or [])
    return False


def open_to_all(params):
    """A security group rule open to 0.0.0.0/0 or ::/0 on a port other than
    80 and 443."""
    permissions = ((params.get("ipPermissions") or {}).get("items")) or []
    for permission in permissions:
        ranges = [item.get("cidrIp") for item in ((permission.get("ipRanges") or {}).get("items") or [])]
        ranges += [item.get("cidrIpv6") for item in ((permission.get("ipv6Ranges") or {}).get("items") or [])]
        if not {"0.0.0.0/0", "::/0"} & set(ranges):
            continue
        low, high = permission.get("fromPort"), permission.get("toPort")
        if permission.get("ipProtocol") == "-1" or low is None or high is None:
            return True
        if not (low == high and low in WEB_PORTS):
            return True
    return False
