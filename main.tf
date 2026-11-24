# The module makes no providers: the caller passes aws, in the region of
# every resource but CloudFront, and aws.global in us-east-1, where CloudFront
# and its certificate live (see README).
# CloudFront and its certificate (on a domain of its own) live in us-east-1.

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}
