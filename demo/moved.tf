# The demo kept its resources at the top of the state until the repository
# became a module (v0.3.0): its state keeps every resource, now under
# module.account.

moved {
  from = aws_cloudwatch_log_delivery_source.access
  to   = module.account.aws_cloudwatch_log_delivery_source.access
}

moved {
  from = aws_cloudwatch_log_delivery_destination.access
  to   = module.account.aws_cloudwatch_log_delivery_destination.access
}

moved {
  from = aws_cloudwatch_log_delivery.access
  to   = module.account.aws_cloudwatch_log_delivery.access
}

moved {
  from = aws_apigatewayv2_api.http
  to   = module.account.aws_apigatewayv2_api.http
}

moved {
  from = aws_apigatewayv2_authorizer.cognito
  to   = module.account.aws_apigatewayv2_authorizer.cognito
}

moved {
  from = aws_apigatewayv2_integration.lambda
  to   = module.account.aws_apigatewayv2_integration.lambda
}

moved {
  from = aws_apigatewayv2_route.api
  to   = module.account.aws_apigatewayv2_route.api
}

moved {
  from = aws_cloudwatch_log_group.api
  to   = module.account.aws_cloudwatch_log_group.api
}

moved {
  from = aws_apigatewayv2_stage.default
  to   = module.account.aws_apigatewayv2_stage.default
}

moved {
  from = aws_lambda_permission.api
  to   = module.account.aws_lambda_permission.api
}

moved {
  from = aws_cloudfront_origin_access_control.web
  to   = module.account.aws_cloudfront_origin_access_control.web
}

moved {
  from = aws_cloudfront_cache_policy.web
  to   = module.account.aws_cloudfront_cache_policy.web
}

moved {
  from = aws_cloudfront_response_headers_policy.web
  to   = module.account.aws_cloudfront_response_headers_policy.web
}

moved {
  from = aws_cloudfront_distribution.web
  to   = module.account.aws_cloudfront_distribution.web
}

moved {
  from = aws_cognito_user_pool.main
  to   = module.account.aws_cognito_user_pool.main
}

moved {
  from = aws_cognito_user_pool_client.web
  to   = module.account.aws_cognito_user_pool_client.web
}

moved {
  from = aws_cognito_user.admin
  to   = module.account.aws_cognito_user.admin
}

moved {
  from = aws_cognito_user_group.admins
  to   = module.account.aws_cognito_user_group.admins
}

moved {
  from = aws_cognito_user_in_group.admin
  to   = module.account.aws_cognito_user_in_group.admin
}

moved {
  from = aws_acm_certificate.web
  to   = module.account.aws_acm_certificate.web
}

moved {
  from = aws_route53_record.certificate
  to   = module.account.aws_route53_record.certificate
}

moved {
  from = aws_acm_certificate_validation.web
  to   = module.account.aws_acm_certificate_validation.web
}

moved {
  from = aws_route53_record.web
  to   = module.account.aws_route53_record.web
}

moved {
  from = aws_dynamodb_table.main
  to   = module.account.aws_dynamodb_table.main
}

moved {
  from = aws_s3_object.web
  to   = module.account.aws_s3_object.web
}

moved {
  from = aws_s3_object.config
  to   = module.account.aws_s3_object.config
}

moved {
  from = aws_iam_role.lambda
  to   = module.account.aws_iam_role.lambda
}

moved {
  from = aws_iam_role_policy.lambda
  to   = module.account.aws_iam_role_policy.lambda
}

moved {
  from = aws_cloudwatch_log_group.lambda
  to   = module.account.aws_cloudwatch_log_group.lambda
}

moved {
  from = aws_lambda_function.app
  to   = module.account.aws_lambda_function.app
}

moved {
  from = aws_lambda_function_event_invoke_config.app
  to   = module.account.aws_lambda_function_event_invoke_config.app
}

moved {
  from = aws_cloudwatch_event_rule.report
  to   = module.account.aws_cloudwatch_event_rule.report
}

moved {
  from = aws_cloudwatch_event_rule.trail
  to   = module.account.aws_cloudwatch_event_rule.trail
}

moved {
  from = aws_cloudwatch_event_rule.logs
  to   = module.account.aws_cloudwatch_event_rule.logs
}

moved {
  from = aws_cloudwatch_event_rule.daily
  to   = module.account.aws_cloudwatch_event_rule.daily
}

moved {
  from = aws_cloudwatch_event_target.report
  to   = module.account.aws_cloudwatch_event_target.report
}

moved {
  from = aws_cloudwatch_event_target.trail
  to   = module.account.aws_cloudwatch_event_target.trail
}

moved {
  from = aws_cloudwatch_event_target.logs
  to   = module.account.aws_cloudwatch_event_target.logs
}

moved {
  from = aws_cloudwatch_event_target.daily
  to   = module.account.aws_cloudwatch_event_target.daily
}

moved {
  from = aws_lambda_permission.events
  to   = module.account.aws_lambda_permission.events
}

moved {
  from = aws_ssm_parameter.admin_password
  to   = module.account.aws_ssm_parameter.admin_password
}

moved {
  from = aws_cloudwatch_log_group.reset
  to   = module.account.aws_cloudwatch_log_group.reset
}

moved {
  from = aws_iam_role.reset
  to   = module.account.aws_iam_role.reset
}

moved {
  from = aws_iam_role_policy.reset
  to   = module.account.aws_iam_role_policy.reset
}

moved {
  from = aws_lambda_function.reset
  to   = module.account.aws_lambda_function.reset
}

moved {
  from = aws_cloudwatch_event_rule.reset
  to   = module.account.aws_cloudwatch_event_rule.reset
}

moved {
  from = aws_cloudwatch_event_target.reset
  to   = module.account.aws_cloudwatch_event_target.reset
}

moved {
  from = aws_lambda_permission.reset
  to   = module.account.aws_lambda_permission.reset
}

moved {
  from = aws_lambda_invocation.first_reset
  to   = module.account.aws_lambda_invocation.first_reset
}

moved {
  from = aws_s3_bucket.web
  to   = module.account.aws_s3_bucket.web
}

moved {
  from = aws_s3_bucket_public_access_block.web
  to   = module.account.aws_s3_bucket_public_access_block.web
}

moved {
  from = aws_s3_bucket_ownership_controls.web
  to   = module.account.aws_s3_bucket_ownership_controls.web
}

moved {
  from = aws_s3_bucket_server_side_encryption_configuration.web
  to   = module.account.aws_s3_bucket_server_side_encryption_configuration.web
}

moved {
  from = aws_s3_bucket_lifecycle_configuration.web
  to   = module.account.aws_s3_bucket_lifecycle_configuration.web
}

moved {
  from = aws_s3_bucket_policy.web
  to   = module.account.aws_s3_bucket_policy.web
}

moved {
  from = aws_sesv2_email_identity.mail
  to   = module.account.aws_sesv2_email_identity.mail
}

moved {
  from = aws_route53_record.mail_dkim
  to   = module.account.aws_route53_record.mail_dkim
}

moved {
  from = aws_sesv2_email_identity_mail_from_attributes.mail
  to   = module.account.aws_sesv2_email_identity_mail_from_attributes.mail
}

moved {
  from = aws_route53_record.mail_from_mx
  to   = module.account.aws_route53_record.mail_from_mx
}

moved {
  from = aws_route53_record.mail_from_spf
  to   = module.account.aws_route53_record.mail_from_spf
}

moved {
  from = aws_route53_record.mail_dmarc
  to   = module.account.aws_route53_record.mail_dmarc
}

moved {
  from = aws_cognito_identity_provider.sso
  to   = module.account.aws_cognito_identity_provider.sso
}

moved {
  from = aws_acm_certificate.auth
  to   = module.account.aws_acm_certificate.auth
}

moved {
  from = aws_route53_record.auth_certificate
  to   = module.account.aws_route53_record.auth_certificate
}

moved {
  from = aws_acm_certificate_validation.auth
  to   = module.account.aws_acm_certificate_validation.auth
}

moved {
  from = aws_cognito_user_pool_domain.auth
  to   = module.account.aws_cognito_user_pool_domain.auth
}

moved {
  from = aws_route53_record.auth
  to   = module.account.aws_route53_record.auth
}

moved {
  from = aws_lambda_permission.cognito
  to   = module.account.aws_lambda_permission.cognito
}
