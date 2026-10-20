data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid = "Logs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }

  statement {
    sid = "Table"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:BatchWriteItem",
    ]
    resources = [aws_dynamodb_table.main.arn]
  }

  # The reports of the costs; a manifest that lists no files makes the
  # function list the month's folder.
  dynamic "statement" {
    for_each = local.costs ? [var.reports_bucket] : []

    content {
      sid       = "ReadReports"
      actions   = ["s3:GetObject"]
      resources = ["arn:aws:s3:::${statement.value}/${var.cur_prefix}/*"]
    }
  }

  dynamic "statement" {
    for_each = local.costs ? [var.reports_bucket] : []

    content {
      sid       = "ListReports"
      actions   = ["s3:ListBucket"]
      resources = ["arn:aws:s3:::${statement.value}"]

      condition {
        test     = "StringLike"
        variable = "s3:prefix"
        values   = ["${var.cur_prefix}/*"]
      }
    }
  }

  dynamic "statement" {
    for_each = local.trail ? [var.trail_bucket] : []

    content {
      sid       = "ReadTrail"
      actions   = ["s3:GetObject"]
      resources = ["arn:aws:s3:::${statement.value}/*"]
    }
  }

  # A load of a period (backfill.py) lists the regions and the days' folders.
  dynamic "statement" {
    for_each = local.trail ? [var.trail_bucket] : []

    content {
      sid       = "ListTrail"
      actions   = ["s3:ListBucket"]
      resources = ["arn:aws:s3:::${statement.value}"]

      condition {
        test     = "StringLike"
        variable = "s3:prefix"
        values   = ["AWSLogs/*"]
      }
    }
  }

  dynamic "statement" {
    for_each = local.trail && var.trail_kms_key_arn != "" ? [var.trail_kms_key_arn] : []

    content {
      sid       = "DecryptTrail"
      actions   = ["kms:Decrypt"]
      resources = [statement.value]
    }
  }

  dynamic "statement" {
    for_each = local.budgets ? [local.account_id] : []

    content {
      sid       = "ReadBudgets"
      actions   = ["budgets:ViewBudget"]
      resources = ["arn:aws:budgets::${statement.value}:budget/*"]
    }
  }

  # The alerts and the digest, only where mails go. In the SES sandbox the
  # recipient has to be a verified identity too.
  dynamic "statement" {
    for_each = local.mail ? [var.sender_address] : []

    content {
      sid       = "SendMail"
      actions   = ["ses:SendEmail"]
      resources = ["arn:aws:ses:${var.region}:${local.account_id}:identity/*"]

      condition {
        test     = "StringEquals"
        variable = "ses:FromAddress"
        values   = [statement.value]
      }
    }
  }

  statement {
    sid = "ManageUsers"
    actions = [
      "cognito-idp:ListUsers",
      "cognito-idp:ListUsersInGroup",
      "cognito-idp:AdminGetUser",
      "cognito-idp:AdminCreateUser",
      "cognito-idp:AdminDeleteUser",
      "cognito-idp:AdminDisableUser",
      "cognito-idp:AdminEnableUser",
      "cognito-idp:AdminSetUserPassword",
      "cognito-idp:AdminSetUserMFAPreference",
      "cognito-idp:AdminAddUserToGroup",
      "cognito-idp:AdminRemoveUserFromGroup",
    ]
    resources = [aws_cognito_user_pool.main.arn]
  }

  # A user of AWS SSO: their groups in IAM Identity Center decide whether
  # they are an administrator.
  dynamic "statement" {
    for_each = local.sso ? [tolist(data.aws_ssoadmin_instances.main[0].identity_store_ids)[0]] : []

    content {
      sid = "IdentityCenterGroups"
      actions = [
        "identitystore:GetUserId",
        "identitystore:ListGroupMembershipsForMember",
        "identitystore:DescribeGroup",
      ]
      resources = [
        "arn:aws:identitystore::${local.account_id}:identitystore/${statement.value}",
        "arn:aws:identitystore:::user/*",
        "arn:aws:identitystore:::group/*",
        "arn:aws:identitystore:::membership/*",
      ]
    }
  }

  # "Refresh" on the page reads the reports in an invocation of its own, and a
  # load of a period goes on in the next.
  statement {
    sid       = "InvokeSelf"
    actions   = ["lambda:InvokeFunction"]
    resources = [local.function_arn]
  }
}

resource "aws_iam_role" "lambda" {
  name               = local.name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "lambda" {
  name   = local.name
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}
