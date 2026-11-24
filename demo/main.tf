# The public demo: the module of this repository at its CloudFront address,
# with the sign-in published on the page, the made-up account of
# seed/seed.json instead of this one, no mails and the hourly reset. The CI of
# the repository deploys it from this directory.
provider "aws" {
  region = var.region

  default_tags {
    tags = var.tags
  }
}

provider "aws" {
  alias  = "global"
  region = "us-east-1"

  default_tags {
    tags = var.tags
  }
}

module "account" {
  source = "../"

  providers = {
    aws        = aws
    aws.global = aws.global
  }

  name               = var.name
  demo               = true
  admin_email        = "demo@example.com"
  admin_password     = "Demo-Password-1"
  reset_schedule     = "rate(1 hour)"
  totp_issuer        = "AWS account demo"
  access_logs_bucket = var.access_logs_bucket
}
