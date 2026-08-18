# Telegram notifications for free Udemy coupon courses

This patch adds a notification-only mode that reuses the existing coupon
scrapers without logging in to Udemy and without enrolling in any course.

## Security

Never put the Telegram bot token in the public repository, `settings.yaml`, or
source code. The notifier reads secrets from environment variables:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Notification history is stored by default at:

`C:\Users\<USER>\.udemy_enroller\notified_courses.json`

The default memory window is 60 days.

## Windows / PowerShell quick start

```powershell
$env:TELEGRAM_BOT_TOKEN="PASTE_YOUR_BOT_TOKEN_HERE"
$env:TELEGRAM_CHAT_ID="PASTE_YOUR_CHAT_ID_HERE"

python run_enroller.py --notify-dry-run --max-pages 2
```

`--notify-dry-run` discovers, validates and filters offers, but does not send
Telegram messages and does not update notification history.

When the dry run looks correct:

```powershell
python run_enroller.py --notify-only
```

## Optional filters

```powershell
$env:UDEMY_NOTIFY_LANGUAGES="English,Polish"
$env:UDEMY_NOTIFY_INCLUDE_KEYWORDS="AI,ChatGPT,Claude,Python,automation,YouTube,marketing,video"
$env:UDEMY_NOTIFY_EXCLUDE_KEYWORDS="forex,cryptocurrency"
$env:UDEMY_NOTIFY_MIN_RATING="4.2"
$env:UDEMY_NOTIFY_MIN_STUDENTS="50"
```

Other supported variables:

- `UDEMY_NOTIFY_CATEGORIES`
- `UDEMY_NOTIFY_MEMORY_DAYS` (default `60`)
- `UDEMY_NOTIFY_HIT_MIN_RATING` (default `4.5`)
- `UDEMY_NOTIFY_HIT_MIN_STUDENTS` (default `1000`)
- `UDEMY_NOTIFY_MAX_HITS` (default `5`)
- `UDEMY_NOTIFY_TIMEOUT` (default `20`)
- `UDEMY_NOTIFY_STATE_FILE`

## Delivery behavior

High-quality HIT courses are sent as individual alerts. Remaining matching
courses are grouped into digest messages.

A course is stored in notification history only after Telegram accepts the
message, so failed sends remain eligible for a later run.

## Existing enroller

Normal enrollment behavior is unchanged. Notification mode returns before the
normal `Settings` object is created, so it does not request Udemy credentials,
does not use the login cookie, and does not call enrollment/checkout.
