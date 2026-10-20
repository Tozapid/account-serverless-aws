# One table. pk = COST, sk = <YYYY-MM>: the costs of a month, summed from the
# report by day, service, region, project and type; pk = BUDGETS,
# sk = SNAPSHOT: the budgets as Budgets last gave them. pk = TRAIL,
# sk = DAY#<YYYY-MM-DD>: the counters of a day of CloudTrail, sk = KNOWN: the
# principals, regions and sign-in addresses seen so far; pk = EVENTS#<day>,
# sk = <time>#<event id>: the events worth showing (changes, errors, sign-ins),
# which expire; pk = FINDINGS, sk = <time>#<rule>#<key>: what the rules found,
# a year. pk = SETTINGS, sk = RULES: the rules administrators turned off, the
# principals they ignore and the expected regions; pk = USER#<email>,
# sk = PREFS: which mails a user wants. pk = SEED, sk = STATE: when the demo
# made its examples (reset.py). pk = BACKFILL, sk = <id>: a load of a
# period from S3 and how far it got. pk = MARK holds one-off markers (a
# finding already raised, an alert already mailed this hour, the digest of a
# day) that keep the same thing from happening twice.
resource "aws_dynamodb_table" "main" {
  #checkov:skip=CKV_AWS_119:An AWS owned key avoids a dedicated KMS charge.
  #checkov:skip=CKV_AWS_28:Point-in-time recovery is off in the demo only, which the reset rebuilds every hour.
  name         = local.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "expires"
    enabled        = true
  }

  # The demo is rebuilt every hour; production keeps 35 days of history.
  point_in_time_recovery {
    enabled = !var.demo
  }

  server_side_encryption {
    enabled = true
  }
}
