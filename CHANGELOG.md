# Changelog

## v0.4.0

### Changed

- The demo is reset six hours after the first change instead of every hour, and not at all while nobody changes anything: most hours of a demo cost nothing now. The first change through the API (any request but a read that succeeds) makes a one-off schedule `<name>-reset` of EventBridge Scheduler, which calls the reset function `reset_delay_hours` later with the role `<name>-reset-scheduler` and then deletes itself; while it waits, further changes make none. The EventBridge rule of the hourly reset is gone. `reset_schedule` became `reset_delay_hours`, a number of hours (`0`, the default, no reset; `6` in the demo).
- The sign-in page of the demo says when it goes back to the start: six hours after a change.
- Without changes the made-up account is not reset either, so it does not take on the records of a new hour or a new day until a visitor changes something; the reset after a change brings it up to that hour, or makes it again on a new day.

## v0.3.1

### Removed

- `demo/moved.tf`: the state of the demo already keeps its resources under `module.account`. A site that still has the resources at the top of its state moves them in its own root module before taking this version.

## v0.3.0

### Changed

- The repository is a Terraform module: the root makes no providers and no backend (the caller passes `aws` and `aws.global`), and a site is a module call pinned to a release tag, `git::https://github.com/Tozapid/account-serverless-aws.git?ref=vX.Y.Z`; a newer version of a site is a newer `ref`. The public demo is a call of the module in `demo/`, which the CI deploys (`working_directory: demo`); `demo/moved.tf` moves its resources under `module.account` in the state. The defaults are those of a production site (`demo = false`, no reset); `name` and `admin_email` have none. New inputs: `ses_identity` (false when the caller manages the SES identity of the domain) and `totp_issuer` (the name in an authenticator app, "AWS account demo" in the demo). The region comes from the provider, the tags from its `default_tags`; the zips of the functions are built in `.build/` of the root module. `examples/production.tfvars` became `examples/production/main.tf`.
- On an iPhone the page no longer zooms in on a tap into a field: fields on touch screens are 16px (Safari zooms into smaller text), and the text size does not change on rotation. A dialog is as high as the visible screen (`100dvh`), not as the screen behind the bars of the browser.

## v0.2.0

### Added

- Traffic: the access logs of the sites and buckets of the account from a bucket (`logs_bucket`): the CloudFront standard logs (v2) under `cloudfront/<site>/` and S3 server access logs under `s3/`, read as they arrive. Per source and day: requests, data sent, client and server errors, cache hits, average time, and the busiest paths or keys, addresses, countries, clients, referrers, methods or operations, requesters and error codes; a chart of 14 days. The demo shows two made-up sites and a bucket.
- Cost Optimization Hub: the recommendations of its Data Export (`coh_prefix` in `reports_bucket`) in a table under Costs, with the monthly savings, the effort and whether a restart is needed; one that saves at least `savings_min_usd` a month is a finding (savings).
- CloudTrail Insights: an unusual rate of write calls or of API errors is a finding (insight), with the baseline, the rate seen, the duration and who made the most calls.
- CloudFront access logs, optional: with `access_logs_bucket` set, the standard logs (v2) of the distribution go to that bucket under `cloudfront/<name>/` as JSON lines, for the Traffic page of this or another overview to read. The CI passes the repository variable `ACCESS_LOGS_BUCKET`; unset, there are none.

### Changed

- Load from S3 takes up to 1830 days at once.

## v0.1.0

### Added

- An overview of an AWS account, serverless on AWS: sign-in with Cognito, optional two-factor authentication, users and administrators, English, Russian, French and Italian.
- Costs from the Cost and Usage Report 2.0: a Data Export to S3, read when its manifest arrives; by day (a chart split by service), service, project (a cost allocation tag), region, line type and usage type; the forecast of the month.
- Budgets from the Budgets API: actual, forecast and limit.
- CloudTrail: every log file read when it arrives; counters by day, the changes, errors and sign-ins with the details of the request; a click on a principal, service, change, error code or region in the lists of a day, or on a value in the table of events, filters by it, the values add up, and the tiles, lists and chart count what the filter keeps, reads included.
- Findings: the root user, sign-in without MFA, CloudTrail and security services changed or turned off, IAM credentials and administrator rights, public access, KMS keys, open ports, unexpected regions, new principals and sign-in addresses, bursts of denied calls, budgets, cost spikes and new paid services. Administrators turn rules off, set the expected regions and the ignored principals, and acknowledge findings.
- Mails through SES: critical findings at once, a daily digest, in the language of the user's page.
- Load from S3: administrators read any days of the CloudTrail logs and the cost reports of their months again, kept for as long as they say, with nothing counted twice and no findings or mails.
- Sign-in through AWS SSO, optional: IAM Identity Center as a SAML identity provider of the user pool, administrators by their groups.
- Charts: a click on the legend hides or shows a series, a double click shows it alone.
- A public demo on the CloudFront address: a made-up account made by the code that reads a real one, made again every day and kept up every hour.
