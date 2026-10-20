resource "aws_cloudwatch_log_group" "lambda" {
  #checkov:skip=CKV_AWS_158:CloudWatch's own encryption is enough for these logs. A customer managed key would add a monthly charge.
  #checkov:skip=CKV_AWS_338:Thirty days is enough to look into a missed report or mail and keeps the log bill small.
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 30
}

# The code without the tests and the reset. Git ignores .build/.
data "archive_file" "app" {
  type = "zip"
  dynamic "source" {
    for_each = toset(["app.py", "common.py", "costs.py", "trail.py", "rules.py", "mail.py", "sso.py", "combos.py", "backfill.py"])

    content {
      content  = file("${path.module}/lambda/${source.value}")
      filename = source.value
    }
  }
  output_path      = "${path.module}/.build/account.zip"
  output_file_mode = "0644"
}

# One function: the API, the reports from S3, the daily job, the loads of a
# period (backfill.py) and the mails.
resource "aws_lambda_function" "app" {
  #checkov:skip=CKV_AWS_50:X-Ray is disabled to avoid extra cost on a low-traffic function.
  #checkov:skip=CKV_AWS_115:Reserved concurrency is left to the account pool.
  #checkov:skip=CKV_AWS_116:A log file or report that fails is read again with the next one of its day or month; the error is in the logs.
  #checkov:skip=CKV_AWS_117:The function calls public AWS endpoints and the release feed on GitHub.
  #checkov:skip=CKV_AWS_173:Environment values are names, addresses and thresholds, not secrets.
  #checkov:skip=CKV_AWS_272:Code signing is not used for this function.
  function_name = local.name
  role          = aws_iam_role.lambda.arn
  runtime       = "python3.14"
  architectures = ["arm64"]
  handler       = "app.handler"
  memory_size   = 512
  # A month of the report or a busy log file; API Gateway still stops the API
  # after 30 seconds.
  timeout = 300

  filename         = data.archive_file.app.output_path
  source_code_hash = data.archive_file.app.output_base64sha256

  environment {
    variables = {
      TABLE_NAME          = aws_dynamodb_table.main.name
      SITE_URL            = local.site_url
      USER_POOL_ID        = aws_cognito_user_pool.main.id
      ADMIN_GROUP         = aws_cognito_user_group.admins.name
      SSO_PROVIDER        = local.sso ? local.sso_provider : ""
      IDENTITY_STORE_ID   = local.sso ? tolist(data.aws_ssoadmin_instances.main[0].identity_store_ids)[0] : ""
      SSO_ADMIN_GROUPS    = join(",", var.sso_admin_groups)
      ACCOUNT_ID          = local.shown_account
      SENDER              = local.mail ? var.sender_address : ""
      VERSION             = local.version
      RELEASES_REPOSITORY = var.releases_repository
      DEMO                = tostring(var.demo)
      REPORTS_BUCKET      = local.costs ? var.reports_bucket : ""
      CUR_PREFIX          = var.cur_prefix
      COST_TAG            = var.cost_tag
      SPIKE_FACTOR        = tostring(var.spike_factor)
      SPIKE_MIN_USD       = tostring(var.spike_min_usd)
      ALLOWED_REGIONS     = join(",", var.allowed_regions)
      EVENT_DAYS          = tostring(var.event_days)
      TRAIL_BUCKET        = local.trail ? var.trail_bucket : ""
      BUDGETS             = tostring(local.budgets)
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda.name
  }

  depends_on = [aws_iam_role_policy.lambda]
}

# Not retried: a report or a log file is read again with the next one, and a
# second digest would be a second mail (the daily job also guards against it).
resource "aws_lambda_function_event_invoke_config" "app" {
  function_name          = aws_lambda_function.app.function_name
  maximum_retry_attempts = 0
}

# A manifest of the Data Export: a refresh of the costs of a month is
# complete. The bucket has to send its events to EventBridge.
resource "aws_cloudwatch_event_rule" "report" {
  count = local.costs ? 1 : 0

  name        = "${local.name}-report"
  description = "A new Cost and Usage Report for ${local.name}."
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [var.reports_bucket] }
      object = { key = [{ wildcard = "${var.cur_prefix}/metadata/*Manifest.json" }] }
    }
  })
}

# A log file of CloudTrail; the digests (CloudTrail-Digest/) do not match.
resource "aws_cloudwatch_event_rule" "trail" {
  count = local.trail ? 1 : 0

  name        = "${local.name}-trail"
  description = "A new CloudTrail log file for ${local.name}."
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [var.trail_bucket] }
      object = { key = [{ wildcard = "*/CloudTrail/*.json.gz" }] }
    }
  })
}

resource "aws_cloudwatch_event_rule" "daily" {
  name                = "${local.name}-daily"
  description         = "Budgets, cost spikes and the daily digest of ${local.name}."
  schedule_expression = var.digest_schedule
}

resource "aws_cloudwatch_event_target" "report" {
  count = local.costs ? 1 : 0

  rule = aws_cloudwatch_event_rule.report[0].name
  arn  = aws_lambda_function.app.arn
}

resource "aws_cloudwatch_event_target" "trail" {
  count = local.trail ? 1 : 0

  rule = aws_cloudwatch_event_rule.trail[0].name
  arn  = aws_lambda_function.app.arn
}

resource "aws_cloudwatch_event_target" "daily" {
  rule  = aws_cloudwatch_event_rule.daily.name
  arn   = aws_lambda_function.app.arn
  input = jsonencode({ action = "daily" })
}

resource "aws_lambda_permission" "events" {
  for_each = merge(
    { daily = aws_cloudwatch_event_rule.daily.arn },
    local.costs ? { report = aws_cloudwatch_event_rule.report[0].arn } : {},
    local.trail ? { trail = aws_cloudwatch_event_rule.trail[0].arn } : {},
  )

  statement_id  = "AllowEventBridge-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app.function_name
  principal     = "events.amazonaws.com"
  source_arn    = each.value
}
