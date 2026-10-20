# Changelog

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
