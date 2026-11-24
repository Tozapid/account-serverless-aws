variable "name" {
  type        = string
  description = "Prefix of every resource name; the page bucket adds the account id."
}

variable "demo" {
  type        = bool
  default     = false
  description = "Public demo: the page shows made-up examples instead of the account (no report, log or budget of the account is read), the sign-in screen shows and fills in the demo sign-in, the settings hide the password and two-factor authentication, and administrators may look at the users but not change them."
}

variable "domain_name" {
  type        = string
  default     = ""
  description = "Name of the page, such as aws.example.com, in the Route 53 zone route53_zone_name. Empty: the page answers at its CloudFront address (the demo)."
}

variable "route53_zone_name" {
  type        = string
  default     = ""
  description = "Public Route 53 zone of domain_name; the stack writes the certificate's validation, the alias records and, with sender_address, the records of the SES identity there."
}

variable "admin_email" {
  type        = string
  description = "Sign-in of the first user, an administrator. Self sign-up is off; administrators invite the others on the page."
}

variable "admin_password" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Permanent password of the first user, published in the demo and put back by the reset. Empty: Cognito mails admin_email a temporary password instead."
}

variable "reset_delay_hours" {
  type        = number
  default     = 0
  description = "Hours after the first change through the API that the demo goes back to its initial state: the table is wiped, the examples of seed/seed.json made again, the first user gets admin_password back and other users go. 0, the default, turns the reset off. Nothing runs on a schedule: a demo nobody changes is not reset."

  validation {
    condition     = var.reset_delay_hours >= 0
    error_message = "reset_delay_hours is a number of hours, 0 for no reset."
  }
}

variable "sender_address" {
  type        = string
  default     = ""
  description = "From address of the alerts and the daily digest, such as alert@aws.example.com, on domain_name, which the stack makes an SES identity (ses.tf). Empty, or the demo: no SES identity and no mails."

  validation {
    condition     = var.sender_address == "" || can(regex("^[^@\\s]+@[^@\\s]+$", var.sender_address))
    error_message = "sender_address is an email address or empty."
  }
}

variable "ses_identity" {
  type        = bool
  default     = true
  description = "Makes domain_name an SES identity for the mails of sender_address (ses.tf). False when the caller manages the SES identity of the domain itself."
}

variable "totp_issuer" {
  type        = string
  default     = "AWS account"
  description = "Name of the site in an authenticator app, next to the email of the user."
}

variable "digest_schedule" {
  type        = string
  default     = "cron(0 6 * * ? *)"
  description = "When the daily job runs (EventBridge, UTC): it reads the budgets, looks for cost spikes and mails the digest of the day before."
}

variable "cost_tag" {
  type        = string
  default     = "Project"
  description = "Tag whose values split the costs by project; it has to be a cost allocation tag of the account."
}

variable "spike_factor" {
  type        = number
  default     = 2
  description = "A day costs this many times the median of the 14 days before it to count as a spike."
}

variable "spike_min_usd" {
  type        = number
  default     = 0.5
  description = "A day under this many dollars is never a spike, whatever the median."
}

variable "allowed_regions" {
  type        = list(string)
  default     = ["eu-central-1", "us-east-1"]
  description = "Regions where changes are expected. A change elsewhere is a finding; administrators can change the list on the page, this is the default."
}

variable "event_days" {
  type        = number
  default     = 90
  description = "How long the table keeps the CloudTrail events it shows (changes, errors, sign-ins); the findings stay a year, the counters of each day for good. The logs themselves stay in the trail's bucket."
}

variable "reports_bucket" {
  type        = string
  default     = ""
  description = "Bucket of the Cost and Usage Report 2.0 (a Data Export) of the account, with EventBridge notifications on. Empty: no costs are read (the demo)."
}

variable "cur_prefix" {
  type        = string
  default     = "cur/cost-and-usage"
  description = "Prefix of the Data Export in reports_bucket: <S3 prefix>/<export name>, with data/ and metadata/ under it."
}

variable "coh_prefix" {
  type        = string
  default     = "coh/cost-optimization"
  description = "Prefix of the Data Export of the recommendations of Cost Optimization Hub in reports_bucket: data/ and metadata/ are under it. Empty: no recommendations are read."
}

variable "savings_min_usd" {
  type        = number
  default     = 1
  description = "A recommendation that saves at least this many dollars a month is a finding the first time it is seen."
}

variable "logs_bucket" {
  type        = string
  default     = ""
  description = "Bucket of the access logs to show under Traffic: standard logs (v2) of CloudFront under cloudfront/<site>/ and S3 server access logs under s3/, with EventBridge notifications on. Empty: no traffic is read (the demo)."
}

variable "access_logs_bucket" {
  type        = string
  default     = ""
  description = "Bucket that the standard logs (v2) of this site's distribution go to, under cloudfront/<name>/, such as the logs_bucket of an overview; its policy has to let delivery.logs.amazonaws.com write there. Empty: no access logs."
}

variable "trail_bucket" {
  type        = string
  default     = ""
  description = "Bucket of the CloudTrail logs of the account, with EventBridge notifications on. Empty: no logs are read (the demo)."
}

variable "trail_kms_key_arn" {
  type        = string
  default     = ""
  description = "KMS key the trail encrypts its logs with, if any; the function gets kms:Decrypt on it. Empty: SSE-S3."
}

variable "sso_metadata_url" {
  type        = string
  default     = ""
  description = "URL of the SAML metadata of the customer managed application of the page in IAM Identity Center (made in the console, see README). Empty: no sign-in through AWS SSO, only the password."
}

variable "sso_domain_name" {
  type        = string
  default     = ""
  description = "Domain of the Cognito sign-in endpoints that IAM Identity Center sends its SAML answer to, such as auth.aws.example.com; a record in route53_zone_name. Needed with sso_metadata_url."
}

variable "sso_admin_groups" {
  type        = list(string)
  default     = []
  description = "Groups of IAM Identity Center whose members are administrators of the site when they sign in through AWS SSO; the others only look."
}

variable "releases_repository" {
  type        = string
  default     = "Tozapid/account-serverless-aws"
  description = "GitHub repository (owner/name) whose releases the settings compare the version of the site with. Empty turns the check off."
}
