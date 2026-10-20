output "site_url" {
  description = "Address of the page."
  value       = local.site_url
}

output "cloudfront_url" {
  description = "CloudFront address of the page."
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "cognito_user_pool_id" {
  description = "Cognito user pool that signs users in."
  value       = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  description = "Public app client id used by the browser."
  value       = aws_cognito_user_pool_client.web.id
}

output "admin_username" {
  description = "Email of the first user."
  value       = var.admin_email
}

output "function_name" {
  description = "Lambda function of the API, the reports and the mails."
  value       = aws_lambda_function.app.function_name
}

output "table_name" {
  description = "DynamoDB table with everything the page shows."
  value       = aws_dynamodb_table.main.name
}

output "sso_acs_url" {
  description = "Application ACS URL of the SAML application in IAM Identity Center; empty without sso_domain_name."
  value       = var.sso_domain_name != "" ? "https://${var.sso_domain_name}/saml2/idpresponse" : ""
}

output "sso_audience" {
  description = "Application SAML audience of the SAML application in IAM Identity Center."
  value       = "urn:amazon:cognito:sp:${aws_cognito_user_pool.main.id}"
}
