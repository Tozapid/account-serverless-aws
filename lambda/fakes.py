"""The AWS services of the tests, in memory: DynamoDB, S3, SES, Cognito,
Budgets, Lambda, IAM Identity Center and SSM. Nothing goes to the network."""

import copy
import gzip
import io
import json
from datetime import datetime, timezone
from decimal import Decimal

import common


class ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


def matches(condition, item):
    """A boto3 key condition (Key(...).eq / begins_with / between / gte, and
    their &) against an item."""
    expression = condition.get_expression()
    operator, values = expression["operator"], expression["values"]
    if operator == "AND":
        return matches(values[0], item) and matches(values[1], item)
    name = values[0].name
    value = item.get(name)
    if value is None:
        return False
    if operator == "=":
        return value == values[1]
    if operator == "begins_with":
        return value.startswith(values[1])
    if operator == "BETWEEN":
        return values[1] <= value <= values[2]
    if operator == ">=":
        return value >= values[1]
    raise NotImplementedError(operator)


def stored(value):
    """As DynamoDB keeps it: no floats."""
    if isinstance(value, float):
        raise TypeError("Float types are not supported. Use Decimal types instead.")
    if isinstance(value, dict):
        if "" in value:
            raise ClientError("ValidationException")
        return {key: stored(item) for key, item in value.items()}
    if isinstance(value, list):
        return [stored(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, (str, Decimal)):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return value


class Batch:
    def __init__(self, table):
        self.table = table

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def put_item(self, Item):
        self.table.put_item(Item=Item)

    def delete_item(self, Key):
        self.table.delete_item(Key=Key)


class FakeTable:
    def __init__(self):
        self.items = {}
        # Writes that lose a race: put_item raises this many conflicts first.
        self.conflicts = 0

    def get_item(self, Key, ConsistentRead=False):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None, ExpressionAttributeValues=None):
        key = (Item["pk"], Item["sk"])
        current = self.items.get(key)
        values = ExpressionAttributeValues or {}
        if ConditionExpression == "attribute_not_exists(pk) OR version = :v":
            if self.conflicts:
                self.conflicts -= 1
                raise ClientError("ConditionalCheckFailedException")
            if current is not None and int(current.get("version", 0)) != values[":v"]:
                raise ClientError("ConditionalCheckFailedException")
        elif ConditionExpression == "attribute_not_exists(pk) OR expires < :now":
            if current is not None and int(current.get("expires", 0)) >= values[":now"]:
                raise ClientError("ConditionalCheckFailedException")
        elif ConditionExpression is not None:
            raise NotImplementedError(ConditionExpression)
        self.items[key] = stored(dict(Item))

    def delete_item(self, Key):
        self.items.pop((Key["pk"], Key["sk"]), None)

    def query(self, KeyConditionExpression, ScanIndexForward=True, Limit=None, ExclusiveStartKey=None):
        found = sorted((item for item in self.items.values() if matches(KeyConditionExpression, item)),
                       key=lambda item: item["sk"], reverse=not ScanIndexForward)
        if ExclusiveStartKey:
            after = ExclusiveStartKey["sk"]
            found = [item for item in found if (item["sk"] < after if not ScanIndexForward else item["sk"] > after)]
        page = found[:Limit] if Limit else found
        result = {"Items": [self.get_item({"pk": item["pk"], "sk": item["sk"]})["Item"] for item in page]}
        if Limit and len(found) > Limit:
            result["LastEvaluatedKey"] = {"pk": page[-1]["pk"], "sk": page[-1]["sk"]}
        return result

    def batch_writer(self):
        return Batch(self)

    def scan(self, ProjectionExpression=None, ExclusiveStartKey=None):
        return {"Items": [copy.deepcopy(item) for item in self.items.values()]}


class Body:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data


class Pager:
    def __init__(self, fn):
        self.fn = fn

    def paginate(self, **kwargs):
        return [self.fn(**kwargs)]


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put(self, bucket, key, data, modified=None):
        self.objects[(bucket, key)] = (data, modified or datetime.now(timezone.utc))

    def put_json_gz(self, bucket, key, payload):
        self.put(bucket, key, gzip.compress(json.dumps(payload).encode("utf-8")))

    def put_csv_gz(self, bucket, key, rows):
        out = io.StringIO()
        import csv
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        self.put(bucket, key, gzip.compress(out.getvalue().encode("utf-8")))

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError("NoSuchKey")
        return {"Body": Body(self.objects[(Bucket, Key)][0])}

    def get_paginator(self, name):
        def listing(Bucket, Prefix, Delimiter=None):
            found = [(key, modified) for (bucket, key), (_, modified) in sorted(self.objects.items())
                     if bucket == Bucket and key.startswith(Prefix)]
            if Delimiter:
                folders = sorted({Prefix + key[len(Prefix):].split(Delimiter, 1)[0] + Delimiter
                                  for key, _ in found if Delimiter in key[len(Prefix):]})
                found = [(key, modified) for key, modified in found if Delimiter not in key[len(Prefix):]]
                return {"Contents": [{"Key": key, "LastModified": modified} for key, modified in found],
                        "CommonPrefixes": [{"Prefix": folder} for folder in folders]}
            return {"Contents": [{"Key": key, "LastModified": modified} for key, modified in found]}
        return Pager(listing)


class FakeSes:
    def __init__(self):
        self.sent = []

    def send_email(self, FromEmailAddress, Destination, Content):
        self.sent.append({"from": FromEmailAddress, "to": Destination["ToAddresses"],
                          "subject": Content["Simple"]["Subject"]["Data"], "text": Content["Simple"]["Body"]["Text"]["Data"]})
        return {"MessageId": str(len(self.sent))}


class FakeCognito:
    def __init__(self):
        self.users = {}
        self.groups = {"admins": set()}

    def add(self, email, locale="", enabled=True, sso=False):
        attributes = [{"Name": "email", "Value": email}] + ([{"Name": "locale", "Value": locale}] if locale else [])
        username = "IdentityCenter_" + email if sso else email
        self.users[username] = {"Username": username, "UserStatus": "EXTERNAL_PROVIDER" if sso else "CONFIRMED",
                                "Enabled": enabled, "Attributes": attributes, "UserCreateDate": datetime.now(timezone.utc)}

    def get_paginator(self, name):
        return Pager(getattr(self, name))

    def list_users(self, UserPoolId):
        return {"Users": list(self.users.values())}

    def list_users_in_group(self, UserPoolId, GroupName):
        return {"Users": [self.users[email] for email in self.groups[GroupName] if email in self.users]}

    def admin_get_user(self, UserPoolId, Username):
        user = self.users.get(Username)
        if user is None:
            raise ClientError("UserNotFoundException")
        return {"Username": Username, "UserMFASettingList": [], "UserStatus": user["UserStatus"],
                "UserAttributes": user["Attributes"], "Enabled": user["Enabled"]}

    def admin_disable_user(self, UserPoolId, Username):
        self.users[Username]["Enabled"] = False

    def admin_create_user(self, UserPoolId, Username, **kwargs):
        if Username in self.users:
            raise ClientError("UsernameExistsException")
        self.add(Username)
        self.users[Username]["UserStatus"] = "FORCE_CHANGE_PASSWORD"

    def admin_delete_user(self, UserPoolId, Username):
        self.users.pop(Username, None)

    def admin_add_user_to_group(self, UserPoolId, Username, GroupName):
        self.groups[GroupName].add(Username)

    # What the reset does to the demo user.
    def admin_enable_user(self, UserPoolId, Username):
        self.users[Username]["Enabled"] = True

    def admin_set_user_password(self, UserPoolId, Username, Password, Permanent):
        self.users[Username]["password"] = Password

    def admin_set_user_mfa_preference(self, UserPoolId, Username, SoftwareTokenMfaSettings):
        self.users[Username]["mfa"] = SoftwareTokenMfaSettings["Enabled"]

    def admin_delete_user_attributes(self, UserPoolId, Username, UserAttributeNames):
        user = self.users[Username]
        user["Attributes"] = [item for item in user["Attributes"] if item["Name"] not in UserAttributeNames]

    def admin_user_global_sign_out(self, UserPoolId, Username):
        self.users[Username]["signed_out"] = True


class FakeSsm:
    def __init__(self):
        self.values = {}

    def get_parameter(self, Name, WithDecryption=False):
        return {"Parameter": {"Value": self.values[Name]}}


class FakeBudgets:
    def __init__(self, budgets):
        self.budgets = budgets

    def get_paginator(self, name):
        return Pager(lambda AccountId: {"Budgets": self.budgets})


class FakeIdentityStore:
    """IAM Identity Center: users by email and the names of their groups."""

    def __init__(self):
        self.users = {}
        self.groups = {}
        self.calls = 0

    def add(self, email, *groups):
        user_id = f"u-{len(self.users)}"
        self.users[email] = (user_id, [self.group(name) for name in groups])

    def group(self, name):
        group_id = f"g-{name}"
        self.groups[group_id] = name
        return group_id

    def get_user_id(self, IdentityStoreId, AlternateIdentifier):
        self.calls += 1
        email = AlternateIdentifier["UniqueAttribute"]["AttributeValue"]
        if email not in self.users:
            raise ClientError("ResourceNotFoundException")
        return {"UserId": self.users[email][0], "IdentityStoreId": IdentityStoreId}

    def get_paginator(self, name):
        def memberships(IdentityStoreId, MemberId):
            groups = next(groups for user_id, groups in self.users.values() if user_id == MemberId["UserId"])
            return {"GroupMemberships": [{"GroupId": group_id} for group_id in groups]}
        return Pager(memberships)

    def describe_group(self, IdentityStoreId, GroupId):
        return {"GroupId": GroupId, "DisplayName": self.groups[GroupId]}


class FakeLambda:
    def __init__(self):
        self.calls = []

    def invoke(self, FunctionName, InvocationType, Payload):
        self.calls.append(json.loads(Payload))
        return {"StatusCode": 202}


def install():
    """Fresh fakes in place of every client; returns them by name."""
    fakes = {
        "table": FakeTable(), "s3": FakeS3(), "sesv2": FakeSes(), "cognito-idp": FakeCognito(),
        "budgets": FakeBudgets([]), "lambda": FakeLambda(), "identitystore": FakeIdentityStore(), "ssm": FakeSsm(),
    }
    common._table = fakes["table"]
    common._clients.clear()
    common._clients.update({name: fake for name, fake in fakes.items() if name != "table"})
    return fakes


def event(method, path, body=None, params=None, email="owner@example.com", admin=False):
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": method}, "authorizer": {"jwt": {"claims": {
            "email": email, "email_verified": "true", **({"cognito:groups": "[admins]"} if admin else {})}}}},
        "queryStringParameters": params or {},
        "body": json.dumps(body) if body is not None else None,
    }
