"""Sign-in through AWS SSO: IAM Identity Center is a SAML identity provider of
the user pool (SSO_PROVIDER); sso.tf.

Cognito calls the function before it issues each token (Pre Token
Generation). For a user who came through Identity Center:

- the administrators' group (ADMIN_GROUP) is in the token if the user is in
  one of SSO_ADMIN_GROUPS in Identity Center, and is not otherwise, whatever
  the user pool says: Identity Center decides, at every sign-in and refresh;
- email is the address in Identity Center, and email_verified "true":
  Identity Center vouches for it, and the API takes only a verified one. The
  address comes as the SAML attribute "email" or, without it, as the subject
  (NameID, ${user:email}), which the identities of the user keep.

The user is found in the Identity Store (IDENTITY_STORE_ID) by email, which
is unique there. If the lookup fails, the user signs in without the
administrators' group. A user with a password is left as Cognito made them.
"""

import json
import os
import time

import common

GROUP_SECONDS = 300
_group_names = {}


def identity(attributes):
    """The identity of Identity Center among those of a federated user, or
    None for a user with a password."""
    provider = os.environ.get("SSO_PROVIDER", "")
    try:
        identities = json.loads(attributes.get("identities") or "[]")
    except ValueError:
        return None
    return next((item for item in identities if provider and item.get("providerName") == provider), None)


def federated(attributes):
    return identity(attributes) is not None


def email_of(attributes):
    """The address of a user of AWS SSO: the attribute email, or the subject."""
    found = str(attributes.get("email") or "").strip().lower()
    if not found:
        subject = str((identity(attributes) or {}).get("userId") or "").strip().lower()
        found = subject if "@" in subject else ""
    return found


def group_names(email):
    """The names of the groups of the user in Identity Center; empty if there
    is no such user."""
    store = os.environ["IDENTITY_STORE_ID"]
    client = common.client("identitystore")
    try:
        user_id = client.get_user_id(
            IdentityStoreId=store,
            AlternateIdentifier={"UniqueAttribute": {"AttributePath": "emails.value", "AttributeValue": email}},
        )["UserId"]
    except Exception as exc:
        if common.error_code(exc) == "ResourceNotFoundException":
            return set()
        raise
    names = set()
    paginator = client.get_paginator("list_group_memberships_for_member")
    for page in paginator.paginate(IdentityStoreId=store, MemberId={"UserId": user_id}):
        for membership in page.get("GroupMemberships", []):
            names.add(group_name(store, membership["GroupId"]))
    return names


def group_name(store, group_id):
    """The display name of a group; kept a few minutes in the warm function."""
    cached = _group_names.get(group_id)
    if cached and time.time() - cached[1] < GROUP_SECONDS:
        return cached[0]
    name = common.client("identitystore").describe_group(IdentityStoreId=store, GroupId=group_id)["DisplayName"]
    _group_names[group_id] = (name, time.time())
    return name


def admin(email):
    wanted = {name for name in os.environ.get("SSO_ADMIN_GROUPS", "").split(",") if name}
    return bool(wanted & group_names(email))


def token(event):
    attributes = (event.get("request") or {}).get("userAttributes") or {}
    if not federated(attributes):
        return event
    email = email_of(attributes)
    admin_group = os.environ.get("ADMIN_GROUP", "admins")
    groups = set(((event["request"].get("groupConfiguration") or {}).get("groupsToOverride")) or [])
    groups.discard(admin_group)
    try:
        if email and admin(email):
            groups.add(admin_group)
    except Exception:
        # Identity Center did not answer: the user signs in, but not as an
        # administrator.
        common.logger.exception("groups of %s not read", email)
    claims = {"email": email, "email_verified": "true"} if email else {}
    event.setdefault("response", {})["claimsOverrideDetails"] = {
        "claimsToAddOrOverride": claims,
        "groupOverrideDetails": {"groupsToOverride": sorted(groups)},
    }
    common.logger.info("token of %s through %s, groups %s", email, os.environ.get("SSO_PROVIDER"), sorted(groups))
    return event
