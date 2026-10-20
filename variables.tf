variable "region" {
  type        = string
  default     = "eu-central-1"
  description = "Region of every resource but CloudFront: the API, the data, the sign-in and the reading of the reports."
}

variable "name" {
  type        = string
  default     = "account-serverless"
  description = "Prefix of every resource name; the page bucket adds the account id. The deploy role of the demo is limited to it."
}

variable "demo" {
  type        = bool
  default     = true
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
  default     = "demo@example.com"
  description = "Sign-in of the first user, an administrator. Self sign-up is off; administrators invite the others on the page."
}

variable "admin_password" {
  type        = string
  default     = "Demo-Password-1"
  sensitive   = true
  description = "Permanent password of the first user, published in the demo and put back by the reset. Empty: Cognito mails admin_email a temporary password instead."
}

variable "reset_schedule" {
  type        = string
  default     = "rate(1 hour)"
  description = "When the demo goes back to its initial state: the table is wiped, the examples of seed/seed.json made again, the first user gets admin_password back and other users go. An empty string turns the reset off."
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

variable "tags" {
  type = map(string)
  default = {
    Managed = "IAC"
    Project = "account-serverless-aws"
  }
  description = "Tags applied to supported resources."
}
