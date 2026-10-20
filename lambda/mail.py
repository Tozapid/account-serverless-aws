"""Mail through SES from SENDER: alerts and the daily digest.

An alert goes out at once with the critical findings of a log file
(trail.py) to every enabled user who wants alerts. The digest goes out once
a day (the daily job) to every enabled user who wants it: the costs of the
month so far, the last day of the report, the budgets, the day of CloudTrail
and the findings of the last 24 hours. A marker (pk = MARK,
sk = digest#<day>) keeps a second run of the job from mailing it again.

Which mails a user wants is pk = USER#<email>, sk = PREFS (digest, alerts;
both on unless turned off in the settings). Each mail is in the language the
user picked on the page (the Cognito attribute "locale"), else in English.
SENDER empty turns the mails off.
"""

import html
import os
from datetime import timedelta

import common
import rules
import sso

FALLBACK = "en"
TEXT = {
    "en": {
        "brand": "AWS account",
        "alert_subject": "AWS: {title}",
        "alert_subject_many": "AWS: {count} critical findings",
        "alert_intro": "The rules found in CloudTrail:",
        "digest_subject": "AWS on {day}: {spent} this month, {findings} findings",
        "costs": "Costs",
        "month": "This month so far",
        "last_day": "Last day in the report ({day})",
        "previous": "Last month",
        "budget": "Budget {name}: {actual} of {limit}, forecast {forecast}",
        "services": "Services this month",
        "no_report": "No cost report yet.",
        "trail": "CloudTrail on {day}",
        "trail_line": "{events} calls, {writes} changes, {errors} errors, {signins} sign-ins",
        "principals": "Most active",
        "findings": "Findings of the last 24 hours",
        "no_findings": "No findings.",
        "open": "Open the overview: {site}",
        "footer": "Which mails you get is set in Settings → Mail.",
        "critical": "critical",
        "warning": "warning",
        "median": "median {median}",
        "forecast": "forecast {forecast}",
        "rules": {
            "root_activity": "The root user was used",
            "console_login_no_mfa": "Console sign-in without MFA",
            "trail_tampering": "CloudTrail was changed or stopped",
            "security_off": "A security service was turned off",
            "iam_credentials": "IAM credentials created or changed",
            "iam_admin": "Administrator rights granted",
            "public_access": "Something was made public",
            "kms_deletion": "A KMS key was disabled or scheduled for deletion",
            "console_login_failed": "Failed console sign-in",
            "open_port": "A security group opened to the internet",
            "iam_change": "IAM changed",
            "account_change": "Account, billing or organization changed",
            "unusual_region": "A change in an unexpected region",
            "new_principal": "A new principal made changes",
            "new_login_ip": "Sign-in from a new address",
            "access_denied": "Many denied calls",
            "budget_exceeded": "A budget is exceeded",
            "budget_forecast": "A budget is forecast to be exceeded",
            "cost_spike": "A cost spike",
            "new_service": "A new paid service",
        },
    },
    "ru": {
        "brand": "Аккаунт AWS",
        "alert_subject": "AWS: {title}",
        "alert_subject_many": "AWS: критичных находок — {count}",
        "alert_intro": "Правила нашли в CloudTrail:",
        "digest_subject": "AWS за {day}: {spent} в этом месяце, находок — {findings}",
        "costs": "Расходы",
        "month": "С начала месяца",
        "last_day": "Последний день в отчёте ({day})",
        "previous": "Прошлый месяц",
        "budget": "Бюджет {name}: {actual} из {limit}, прогноз {forecast}",
        "services": "Сервисы за месяц",
        "no_report": "Отчёта о расходах ещё нет.",
        "trail": "CloudTrail за {day}",
        "trail_line": "вызовов {events}, изменений {writes}, ошибок {errors}, входов {signins}",
        "principals": "Самые активные",
        "findings": "Находки за 24 часа",
        "no_findings": "Находок нет.",
        "open": "Открыть обзор: {site}",
        "footer": "Какие письма приходят, задаётся в «Настройки» → «Письма».",
        "critical": "критично",
        "warning": "внимание",
        "median": "медиана {median}",
        "forecast": "прогноз {forecast}",
        "rules": {
            "root_activity": "Использован пользователь root",
            "console_login_no_mfa": "Вход в консоль без MFA",
            "trail_tampering": "CloudTrail изменён или остановлен",
            "security_off": "Выключен сервис безопасности",
            "iam_credentials": "Созданы или изменены учётные данные IAM",
            "iam_admin": "Выданы права администратора",
            "public_access": "Что-то стало публичным",
            "kms_deletion": "Ключ KMS выключен или назначен к удалению",
            "console_login_failed": "Неудачный вход в консоль",
            "open_port": "Группа безопасности открыта в интернет",
            "iam_change": "Изменения в IAM",
            "account_change": "Изменения аккаунта, оплаты или организации",
            "unusual_region": "Изменение в неожиданном регионе",
            "new_principal": "Изменения от нового участника",
            "new_login_ip": "Вход с нового адреса",
            "access_denied": "Много отказов в доступе",
            "budget_exceeded": "Бюджет превышен",
            "budget_forecast": "Бюджет будет превышен по прогнозу",
            "cost_spike": "Скачок расходов",
            "new_service": "Новый платный сервис",
        },
    },
    "fr": {
        "brand": "Compte AWS",
        "alert_subject": "AWS : {title}",
        "alert_subject_many": "AWS : {count} alertes critiques",
        "alert_intro": "Les règles ont trouvé dans CloudTrail :",
        "digest_subject": "AWS le {day} : {spent} ce mois-ci, {findings} alertes",
        "costs": "Coûts",
        "month": "Depuis le début du mois",
        "last_day": "Dernier jour du rapport ({day})",
        "previous": "Le mois dernier",
        "budget": "Budget {name} : {actual} sur {limit}, prévision {forecast}",
        "services": "Services ce mois-ci",
        "no_report": "Pas encore de rapport de coûts.",
        "trail": "CloudTrail le {day}",
        "trail_line": "{events} appels, {writes} modifications, {errors} erreurs, {signins} connexions",
        "principals": "Les plus actifs",
        "findings": "Alertes des dernières 24 heures",
        "no_findings": "Aucune alerte.",
        "open": "Ouvrir l’aperçu : {site}",
        "footer": "Les e-mails reçus se règlent dans Paramètres → E-mails.",
        "critical": "critique",
        "warning": "attention",
        "median": "médiane {median}",
        "forecast": "prévision {forecast}",
        "rules": {
            "root_activity": "L’utilisateur root a été utilisé",
            "console_login_no_mfa": "Connexion à la console sans MFA",
            "trail_tampering": "CloudTrail a été modifié ou arrêté",
            "security_off": "Un service de sécurité a été désactivé",
            "iam_credentials": "Identifiants IAM créés ou modifiés",
            "iam_admin": "Droits d’administrateur accordés",
            "public_access": "Une ressource a été rendue publique",
            "kms_deletion": "Une clé KMS désactivée ou programmée pour suppression",
            "console_login_failed": "Échec de connexion à la console",
            "open_port": "Un groupe de sécurité ouvert à Internet",
            "iam_change": "IAM modifié",
            "account_change": "Compte, facturation ou organisation modifiés",
            "unusual_region": "Une modification dans une région inattendue",
            "new_principal": "Un nouveau principal a fait des modifications",
            "new_login_ip": "Connexion depuis une nouvelle adresse",
            "access_denied": "Beaucoup d’appels refusés",
            "budget_exceeded": "Un budget est dépassé",
            "budget_forecast": "Un budget sera dépassé selon la prévision",
            "cost_spike": "Un pic de coûts",
            "new_service": "Un nouveau service payant",
        },
    },
    "it": {
        "brand": "Account AWS",
        "alert_subject": "AWS: {title}",
        "alert_subject_many": "AWS: {count} rilevamenti critici",
        "alert_intro": "Le regole hanno trovato in CloudTrail:",
        "digest_subject": "AWS il {day}: {spent} questo mese, {findings} rilevamenti",
        "costs": "Costi",
        "month": "Dall’inizio del mese",
        "last_day": "Ultimo giorno nel report ({day})",
        "previous": "Mese scorso",
        "budget": "Budget {name}: {actual} su {limit}, previsione {forecast}",
        "services": "Servizi di questo mese",
        "no_report": "Ancora nessun report dei costi.",
        "trail": "CloudTrail il {day}",
        "trail_line": "{events} chiamate, {writes} modifiche, {errors} errori, {signins} accessi",
        "principals": "I più attivi",
        "findings": "Rilevamenti delle ultime 24 ore",
        "no_findings": "Nessun rilevamento.",
        "open": "Apri la panoramica: {site}",
        "footer": "Le email che ricevi si scelgono in Impostazioni → Email.",
        "critical": "critico",
        "warning": "attenzione",
        "median": "mediana {median}",
        "forecast": "previsione {forecast}",
        "rules": {
            "root_activity": "È stato usato l’utente root",
            "console_login_no_mfa": "Accesso alla console senza MFA",
            "trail_tampering": "CloudTrail modificato o fermato",
            "security_off": "Un servizio di sicurezza è stato disattivato",
            "iam_credentials": "Credenziali IAM create o modificate",
            "iam_admin": "Concessi diritti di amministratore",
            "public_access": "Qualcosa è stato reso pubblico",
            "kms_deletion": "Una chiave KMS disattivata o pianificata per l’eliminazione",
            "console_login_failed": "Accesso alla console non riuscito",
            "open_port": "Un gruppo di sicurezza aperto a Internet",
            "iam_change": "IAM modificato",
            "account_change": "Account, fatturazione o organizzazione modificati",
            "unusual_region": "Una modifica in una regione inattesa",
            "new_principal": "Un nuovo principal ha fatto modifiche",
            "new_login_ip": "Accesso da un nuovo indirizzo",
            "access_denied": "Molte chiamate negate",
            "budget_exceeded": "Un budget è superato",
            "budget_forecast": "Un budget sarà superato secondo la previsione",
            "cost_spike": "Un picco di costi",
            "new_service": "Un nuovo servizio a pagamento",
        },
    },
}


# Recipients ---------------------------------------------------------------------------

def prefs(email):
    item = common.get("USER#" + email, "PREFS") or {}
    return {"digest": item.get("digest", True) is not False, "alerts": item.get("alerts", True) is not False}


def recipients(kind):
    """Enabled users who want this kind of mail, with their language."""
    found = []
    paginator = common.client("cognito-idp").get_paginator("list_users")
    for page in paginator.paginate(UserPoolId=os.environ["USER_POOL_ID"]):
        for user in page.get("Users", []):
            if not user.get("Enabled", True):
                continue
            attributes = {item.get("Name"): item.get("Value", "") for item in user.get("Attributes", [])}
            email = sso.email_of(attributes)
            if not email or not prefs(email)[kind]:
                continue
            code = attributes.get("locale", "")[:2].lower()
            found.append((email, code if code in TEXT else FALLBACK))
    # A password and AWS SSO make two users with one email: one mail.
    return list(dict(found).items())


def send(to, subject, text, markup):
    sender = os.environ.get("SENDER", "")
    if not sender:
        return False
    try:
        common.client("sesv2").send_email(
            FromEmailAddress=f"{TEXT['en']['brand']} <{sender}>",
            Destination={"ToAddresses": [to]},
            Content={"Simple": {
                "Subject": {"Data": subject[:200], "Charset": "UTF-8"},
                "Body": {"Text": {"Data": text, "Charset": "UTF-8"}, "Html": {"Data": markup, "Charset": "UTF-8"}},
            }},
        )
    except Exception:
        # In the SES sandbox an address that is not verified is refused; the
        # others still get theirs.
        common.logger.exception("mail to %s not sent", to)
        return False
    return True


# Formatting -------------------------------------------------------------------------

def money(value, unit="USD"):
    sign = "-" if float(value) < 0 else ""
    symbol = "$" if unit == "USD" else unit + " "
    return f"{sign}{symbol}{abs(float(value)):,.2f}"


def describe(item, words):
    """One line of a finding: its title and what it was about."""
    title = words["rules"].get(item["rule"], item["rule"])
    details = item.get("details") or {}
    rule = item["rule"]
    if rule in ("budget_exceeded", "budget_forecast"):
        unit = details.get("unit", "USD")
        parts = [f"{details.get('budget', '')}: {money(details.get('actual', 0), unit)} / {money(details.get('limit', 0), unit)}",
                 words["forecast"].format(forecast=money(details.get("forecast", 0), unit))]
    elif rule == "cost_spike":
        parts = [details.get("day", ""), money(details.get("amount", 0)), words["median"].format(median=money(details.get("median", 0)))]
    elif rule == "new_service":
        parts = [details.get("service", ""), money(details.get("amount", 0))]
    elif rule == "access_denied":
        parts = [item.get("principal", ""), item.get("day", "")]
    else:
        parts = [item.get("event_name", ""), item.get("principal", ""), item.get("ip", ""), item.get("region", ""),
                 (item.get("time") or "").replace("T", " ").replace("Z", " UTC")]
    return f"[{words[item['severity']]}] {title}: " + " · ".join(str(part) for part in parts if part)


def page(lines, words):
    """The text and the HTML of a mail from its lines; a line that starts
    with "# " is a heading."""
    site = os.environ.get("SITE_URL", "")
    tail = ["", words["open"].format(site=site), words["footer"]]
    text = "\n".join(line[2:] if line.startswith("# ") else line for line in lines + tail) + "\n"
    parts = []
    for line in lines:
        if line.startswith("# "):
            parts.append(f'<h3 style="margin:18px 0 6px;font-size:15px">{html.escape(line[2:])}</h3>')
        elif line:
            parts.append(f'<div style="margin:2px 0">{html.escape(line)}</div>')
    parts.append(f'<p style="margin-top:18px"><a href="{html.escape(site)}">{html.escape(site)}</a></p>')
    parts.append(f'<p style="color:#5f6673;font-size:13px">{html.escape(words["footer"])}</p>')
    markup = ('<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;font-size:14px;'
              'line-height:1.5;color:#16181d">' + "".join(parts) + "</div>")
    return text, markup


# Alerts -----------------------------------------------------------------------------

def send_alerts(findings):
    sent = 0
    for email, code in recipients("alerts"):
        words = TEXT[code]
        if len(findings) == 1:
            subject = words["alert_subject"].format(title=words["rules"].get(findings[0]["rule"], findings[0]["rule"]))
        else:
            subject = words["alert_subject_many"].format(count=len(findings))
        text, markup = page([words["alert_intro"], ""] + [describe(item, words) for item in findings], words)
        sent += send(email, subject, text, markup)
    return sent


# Digest -----------------------------------------------------------------------------

def digest_data(day):
    month = common.get("COST", day[:7]) or {}
    first = common.days_before(day[:7] + "-01", 1)
    previous = common.get("COST", first[:7]) or {}
    since = common.iso(common.now_utc() - timedelta(hours=24))
    findings = common.query_all("FINDINGS", start=since)
    return {
        "day": day,
        "month": month,
        "previous": previous,
        "budgets": (common.get("BUDGETS", "SNAPSHOT") or {}).get("budgets") or [],
        "trail": common.get("TRAIL", "DAY#" + day) or {},
        "findings": sorted(findings, key=lambda item: (item["severity"] != rules.CRITICAL, item["sk"])),
    }


def digest_lines(data, words):
    month, trail = data["month"], data["trail"]
    lines = ["# " + words["costs"]]
    if month:
        unit = month.get("currency", "USD")
        lines.append(f"{words['month']}: {money(month.get('total', 0), unit)}")
        usage = month.get("usage_days") or {}
        if usage:
            last = max(usage)
            lines.append(f"{words['last_day'].format(day=last)}: {money(usage[last], unit)}")
        if data["previous"]:
            lines.append(f"{words['previous']}: {money(data['previous'].get('total', 0), unit)}")
        services = sorted((month.get("services") or {}).items(), key=lambda pair: float(pair[1]), reverse=True)[:5]
        if services:
            lines.append(words["services"] + ": " + ", ".join(f"{name} {money(value, unit)}" for name, value in services))
    else:
        lines.append(words["no_report"])
    for budget in data["budgets"]:
        unit = budget.get("unit", "USD")
        lines.append(words["budget"].format(name=budget["name"], actual=money(budget.get("actual", 0), unit),
                                            limit=money(budget.get("limit", 0), unit), forecast=money(budget.get("forecast", 0), unit)))
    lines += ["", "# " + words["trail"].format(day=data["day"])]
    lines.append(words["trail_line"].format(**{key: int(trail.get(key, 0)) for key in ("events", "writes", "errors", "signins")}))
    principals = sorted((trail.get("principals") or {}).items(), key=lambda pair: int(pair[1]), reverse=True)[:5]
    if principals:
        lines.append(words["principals"] + ": " + ", ".join(f"{name} ({int(value)})" for name, value in principals))
    lines += ["", "# " + words["findings"]]
    lines += [describe(item, words) for item in data["findings"][:30]] or [words["no_findings"]]
    return lines


def send_digest(day):
    if not os.environ.get("SENDER") or not common.mark(f"digest#{day}", 3 * 86400):
        return 0
    data = digest_data(day)
    sent = 0
    for email, code in recipients("digest"):
        words = TEXT[code]
        spent = money((data["month"] or {}).get("total", 0), (data["month"] or {}).get("currency", "USD"))
        subject = words["digest_subject"].format(day=day, spent=spent, findings=len(data["findings"]))
        text, markup = page(digest_lines(data, words), words)
        sent += send(email, subject, text, markup)
    return sent
