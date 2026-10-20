# Sign-in through AWS SSO. IAM Identity Center is a SAML identity provider of
# the user pool: the page sends the user to the sign-in domain, which passes
# them straight on to Identity Center (the AWS access portal), and the answer
# comes back to the page as a code. Who may sign in is the assignment of the
# application to groups in IAM Identity Center; who is an administrator,
# their groups (sso_admin_groups), looked up at every token.
#
# The sign-in domain (sso_domain_name) is a record of route53_zone_name, so
# AWS SSO needs the page on a domain of its own; the demo has none.
#
# Identity Center makes customer managed SAML applications in the console
# only (the API does OAuth applications alone): its ACS URL and audience are
# the outputs sso_acs_url and sso_audience, its metadata URL goes into
# sso_metadata_url (README). Until then all of this is off.

data "aws_ssoadmin_instances" "main" {
  count = local.sso ? 1 : 0
}

resource "aws_cognito_identity_provider" "sso" {
  count = local.sso ? 1 : 0

  user_pool_id  = aws_cognito_user_pool.main.id
  provider_name = local.sso_provider
  provider_type = "SAML"

  provider_details = {
    MetadataURL = var.sso_metadata_url
    IDPSignout  = "false"
  }

  # The email of the user in Identity Center, sent as the attribute "email".
  attribute_mapping = {
    email = "email"
  }

  lifecycle {
    # Cognito adds these from the metadata.
    ignore_changes = [
      provider_details["ActiveEncryptionCertificate"],
      provider_details["SLORedirectBindingURI"],
      provider_details["SSORedirectBindingURI"],
    ]

    precondition {
      condition     = local.custom_domain && var.sso_domain_name != ""
      error_message = "sso_metadata_url needs domain_name and sso_domain_name: the sign-in domain is a record in route53_zone_name."
    }
  }
}

# The sign-in domain: Cognito serves it through a CloudFront distribution of
# its own, with a certificate in us-east-1.
resource "aws_acm_certificate" "auth" {
  count = local.sso ? 1 : 0

  provider          = aws.global
  domain_name       = var.sso_domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "auth_certificate" {
  for_each = local.sso ? {
    for dvo in aws_acm_certificate.auth[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  allow_overwrite = true
  zone_id         = data.aws_route53_zone.web[0].zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 60
  records         = [each.value.record]
}

resource "aws_acm_certificate_validation" "auth" {
  count = local.sso ? 1 : 0

  provider                = aws.global
  certificate_arn         = aws_acm_certificate.auth[0].arn
  validation_record_fqdns = [for record in aws_route53_record.auth_certificate : record.fqdn]

  timeouts {
    create = "20m"
  }
}

resource "aws_cognito_user_pool_domain" "auth" {
  count = local.sso ? 1 : 0

  domain          = var.sso_domain_name
  certificate_arn = aws_acm_certificate_validation.auth[0].certificate_arn
  user_pool_id    = aws_cognito_user_pool.main.id

  # A custom domain needs an address record on its parent domain.
  depends_on = [aws_route53_record.web]
}

resource "aws_route53_record" "auth" {
  count = local.sso ? 1 : 0

  zone_id = data.aws_route53_zone.web[0].zone_id
  name    = var.sso_domain_name
  type    = "A"

  alias {
    name                   = aws_cognito_user_pool_domain.auth[0].cloudfront_distribution
    zone_id                = aws_cognito_user_pool_domain.auth[0].cloudfront_distribution_zone_id
    evaluate_target_health = false
  }
}

resource "aws_lambda_permission" "cognito" {
  count = local.sso ? 1 : 0

  statement_id  = "AllowCognito"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.main.arn
}
