locals {
  name       = var.name
  account_id = data.aws_caller_identity.current.account_id
  web_bucket = "${local.name}-web-${local.account_id}"
  # Known before the function exists, so that its policy may let it invoke
  # itself: "Refresh" and a load of a period hand their work to an
  # invocation of its own.
  function_arn = "arn:aws:lambda:${var.region}:${local.account_id}:function:${local.name}"

  # The page on its own domain, or on the CloudFront address without one.
  custom_domain = var.domain_name != ""
  site_host     = local.custom_domain ? var.domain_name : aws_cloudfront_distribution.web.domain_name
  site_url      = "https://${local.site_host}"

  # The demo reads nothing of the account: its costs, logs and budgets are
  # the examples of seed/seed.json.
  costs   = !var.demo && var.reports_bucket != ""
  trail   = !var.demo && var.trail_bucket != ""
  budgets = !var.demo
  reset   = var.reset_schedule != ""
  # The alerts and the digest by mail (ses.tf), from sender_address on
  # domain_name. The demo sends nothing.
  mail = !var.demo && var.sender_address != ""
  # The account the page names: a made-up one in the demo.
  shown_account = var.demo ? "123456789012" : local.account_id

  # Sign-in through AWS SSO (sso.tf): IAM Identity Center is a SAML identity
  # provider of the user pool, under this name. It needs a domain.
  sso          = var.sso_metadata_url != ""
  sso_provider = "IdentityCenter"

  # The version of this deployment: the newest entry of CHANGELOG.md.
  version = regex("(?m)^## (v[0-9]+\\.[0-9]+\\.[0-9]+)(?:\\s|$)", file("${path.module}/CHANGELOG.md"))[0]

  csp = join("; ", [
    "default-src 'self'",
    # Only the two libraries of the page, not all of jsDelivr.
    "script-src 'self' https://cdn.jsdelivr.net/npm/amazon-cognito-identity-js@6.3.15/ https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/",
    "style-src 'self'",
    "img-src 'self' data:",
    # The token endpoint of the sign-in domain, for AWS SSO.
    "connect-src 'self' https://cognito-idp.${var.region}.amazonaws.com${local.sso ? " https://${var.sso_domain_name}" : ""}",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ])
}
