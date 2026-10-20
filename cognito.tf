resource "aws_cognito_user_pool" "main" {
  name                     = local.name
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  # Each user turns on an authenticator app in the settings if they want to.
  mfa_configuration = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  admin_create_user_config {
    allow_admin_create_user_only = true

    invite_message_template {
      email_subject = "AWS account overview invitation"
      email_message = "You have been invited to the overview of the AWS account at ${local.site_url}<br><br>Sign-in: {username}<br>Temporary password: {####}<br><br>You will choose your own password at first sign-in. The temporary one works for 7 days."
      sms_message   = "AWS ${local.site_url}: sign-in {username}, temporary password {####}"
    }
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  # A user who signs in through AWS SSO gets the administrators' group from
  # IAM Identity Center at every token (app.py → sso.py). The function is
  # named by its ARN: the pool is in its environment.
  dynamic "lambda_config" {
    for_each = local.sso ? [local.function_arn] : []

    content {
      pre_token_generation = lambda_config.value
    }
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "${local.name}-web"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false
  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_PASSWORD_AUTH",
  ]

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  # The page sends AWS SSO users to the sign-in domain straight on to IAM
  # Identity Center (identity_provider) and takes the code back with PKCE.
  allowed_oauth_flows_user_pool_client = local.sso
  allowed_oauth_flows                  = local.sso ? ["code"] : []
  # aws.cognito.signin.user.admin: the page reads and writes the language of
  # the user (GetUser, UpdateUserAttributes) with the access token.
  allowed_oauth_scopes         = local.sso ? ["openid", "email", "profile", "aws.cognito.signin.user.admin"] : []
  callback_urls                = local.sso ? ["${local.site_url}/"] : []
  logout_urls                  = local.sso ? ["${local.site_url}/"] : []
  supported_identity_providers = local.sso ? ["COGNITO", aws_cognito_identity_provider.sso[0].provider_name] : ["COGNITO"]

  read_attributes = ["email", "email_verified", "locale"]
  # The email names the user in the API, so users cannot change it. An empty
  # list would let the client write every standard attribute, hence locale.
  write_attributes = ["locale"]
}

# With admin_password the first user gets it as a permanent password and no
# mail; without, Cognito mails admin_email a temporary password.
resource "aws_cognito_user" "admin" {
  user_pool_id             = aws_cognito_user_pool.main.id
  username                 = var.admin_email
  password                 = var.admin_password != "" ? var.admin_password : null
  message_action           = var.admin_password != "" ? "SUPPRESS" : null
  desired_delivery_mediums = var.admin_password != "" ? null : ["EMAIL"]

  attributes = {
    email          = var.admin_email
    email_verified = "true"
  }

  # The user may set their own password and language; apply must not take
  # them back (the demo reset does that on its own).
  lifecycle {
    ignore_changes = [password, temporary_password, attributes["locale"]]
  }
}

# Members manage the accounts, the rules of the findings and acknowledge
# findings in the settings.
resource "aws_cognito_user_group" "admins" {
  name         = "admins"
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "Administrators: manage the accounts and the rules of the findings."
}

resource "aws_cognito_user_in_group" "admin" {
  user_pool_id = aws_cognito_user_pool.main.id
  group_name   = aws_cognito_user_group.admins.name
  username     = aws_cognito_user.admin.username
}
