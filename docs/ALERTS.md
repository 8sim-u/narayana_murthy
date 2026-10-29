# WAMSS Real-Time Alerts

When the backend detects a strain alert or a security anomaly, it can notify
your team instantly over three optional channels: SMTP email, a Telegram bot,
or a generic JSON webhook (which also covers WhatsApp, Slack, and PagerDuty
via a provider webhook).

All channels are OFF by default. Nothing is sent anywhere until you enable a
channel and provide its configuration. If a channel fails (bad credentials,
network down), the failure is logged and the API request that fired the alert
still completes normally.

## Which events trigger alerts

| Alert kind             | Trigger                                                                 |
|------------------------|-------------------------------------------------------------------------|
| `HIGH_STRAIN_DETECTED` | Strain score at or above `WAMSS_STRAIN_ALERT_THRESHOLD` (default 70) for `WAMSS_STRAIN_ALERT_BATCHES` (default 3) consecutive ingest batches from the same machine and employee. |
| `usb_insert`           | A USB device is inserted (severity high).                               |
| `usb_remove`           | A USB device is removed (severity high).                                |
| `file_write_burst`     | Writes at or above `WAMSS_DLP_WRITE_THRESHOLD_MB` (default 500 MB) in one burst (severity medium). |
| `after_hours_activity` | Activity outside `WAMSS_WORK_HOURS` (default 09:00-18:00 in `WAMSS_SECURITY_TZ`, default UTC) (severity medium). |

## Environment variables

| Variable                    | Required by | Purpose                                  |
|-----------------------------|-------------|------------------------------------------|
| `WAMSS_ALERT_EMAIL_ENABLED` | email       | Set to `1` to enable SMTP email alerts.  |
| `WAMSS_ALERT_SMTP_HOST`     | email       | SMTP server hostname.                    |
| `WAMSS_ALERT_SMTP_PORT`     | email       | SMTP port, default `587` (STARTTLS).     |
| `WAMSS_ALERT_SMTP_USER`     | email       | SMTP username (optional if server allows anonymous relay). |
| `WAMSS_ALERT_SMTP_PASS`     | email       | SMTP password.                           |
| `WAMSS_ALERT_SMTP_FROM`     | email       | Sender address shown on the alert mail.  |
| `WAMSS_ALERT_EMAIL_TO`      | email       | Comma-separated recipient addresses.     |
| `WAMSS_TELEGRAM_ENABLED`    | Telegram    | Set to `1` to enable Telegram alerts.    |
| `WAMSS_TELEGRAM_BOT_TOKEN`  | Telegram    | Bot token from BotFather (see below).    |
| `WAMSS_TELEGRAM_CHAT_ID`    | Telegram    | Chat, group, or channel ID to notify.    |
| `WAMSS_WEBHOOK_ENABLED`     | webhook     | Set to `1` to enable webhook alerts.     |
| `WAMSS_WEBHOOK_URL`         | webhook     | HTTPS URL that receives a POST per alert.|

Example:

```bash
export WAMSS_ALERT_EMAIL_ENABLED=1
export WAMSS_ALERT_SMTP_HOST=smtp.example.com
export WAMSS_ALERT_SMTP_PORT=587
export WAMSS_ALERT_SMTP_USER=alerts@example.com
export WAMSS_ALERT_SMTP_PASS='<your password>'
export WAMSS_ALERT_SMTP_FROM=alerts@example.com
export WAMSS_ALERT_EMAIL_TO=soc@example.com,oncall@example.com

export WAMSS_TELEGRAM_ENABLED=1
export WAMSS_TELEGRAM_BOT_TOKEN='<bot token>'
export WAMSS_TELEGRAM_CHAT_ID='<chat id>'

export WAMSS_WEBHOOK_ENABLED=1
export WAMSS_WEBHOOK_URL=https://hooks.example.com/wamss
```

## Setting up each channel

### Email (SMTP)

Any SMTP server works: your company mail relay, Gmail, or an outbound
service. Use port 587 with STARTTLS (the default) or 465/25 if your server
requires it. The sender (`WAMSS_ALERT_SMTP_FROM`) should be an address your
server is allowed to send from. Alerts arrive with the subject
`[WAMSS] <event kind>` and a plain-text summary in the body.

### Telegram bot

1. Open Telegram and message **@BotFather**.
2. Send `/newbot`, give it a name and a username ending in `bot`.
3. BotFather replies with a bot token. Keep it private; it is a credential.
4. Open a chat with your new bot and send any message.
5. Find your chat ID: open `https://api.telegram.org/bot<token>/getUpdates`
   in a browser and look for `"chat":{"id":...}`.
6. To notify a group instead, add the bot to the group and use the group ID
   from the same endpoint.

Set the token as `WAMSS_TELEGRAM_BOT_TOKEN` and the chat ID as
`WAMSS_TELEGRAM_CHAT_ID`. Alerts arrive as plain-text messages.

### Webhook (generic JSON POST)

Point `WAMSS_WEBHOOK_URL` at any HTTPS endpoint. Each alert is POSTed as JSON:

```json
{
  "kind": "usb_insert",
  "payload": {
    "machine_id": "WS-1",
    "employee_id": "EMP-42",
    "severity": "high",
    "reason": "USB device inserted: Kingston DataTraveler",
    "timestamp": "2026-10-30T03:55:00+00:00",
    "detail": {"device": "Kingston DataTraveler"}
  },
  "text": "[WAMSS] USB device inserted\nemployee_id: EMP-42\n...",
  "timestamp": "2026-10-30T03:55:01+00:00",
  "source": "wamss"
}
```

This covers WhatsApp, Slack, and PagerDuty: point the URL at your provider's
incoming-webhook endpoint (for example a Slack incoming webhook or a Twilio
WhatsApp message endpoint) and it receives the JSON body above.

## Code reference

- Dispatcher: `backend/app/alerts.py` (`dispatch_alert(kind, payload)`)
- Hook points: `backend/app/activity.py` (`HIGH_STRAIN_DETECTED`) and
  `backend/app/security.py` (USB, file-write burst, after-hours)
- Tests: `backend/tests/test_alerts.py`
