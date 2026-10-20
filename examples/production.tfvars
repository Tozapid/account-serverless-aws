# Production: your own account, domain and mails, no public sign-in and no
# hourly reset. Copy to terraform.tfvars and fill in.

name = "account"
demo = false

domain_name       = "aws.example.com"
route53_zone_name = "example.com"

admin_email    = "you@example.com"
admin_password = "" # Cognito mails a temporary password instead

reset_schedule = ""

# The Cost and Usage Report 2.0 (a Data Export) and the CloudTrail logs of
# the account; both buckets send their events to EventBridge (see README).
reports_bucket = "cost-reports-123456789012"
cur_prefix     = "cur/cost-and-usage"
trail_bucket   = "aws-cloudtrail-logs-123456789012"

# Mails critical findings at once and the daily digest; the stack makes
# domain_name an SES identity. "" sends no mails.
sender_address = "alert@aws.example.com"

# Sign-in through AWS SSO (IAM Identity Center), optional; see README.
# sso_metadata_url = "https://portal.sso.eu-central-1.amazonaws.com/saml/metadata/..."
# sso_domain_name  = "auth.aws.example.com"
# sso_admin_groups = ["Admins"]
