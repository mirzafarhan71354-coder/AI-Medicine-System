# Medicine reminders and tablet inventory

The reminder dashboard is available at `/dashboard`. Accounts are separate: medicine plans, dose records, tablet inventory, and notification subscriptions are scoped to the signed-in user. Passwords are stored as Werkzeug scrypt hashes. The database is created and upgraded additively at startup.

## Local development

Install dependencies and run the Flask application and reminder worker in separate terminals. On Windows PowerShell:

```powershell
py -m pip install -r requirements.txt
$env:CAREPOINT_SECRET_KEY = "replace-with-a-long-random-secret"
$env:CAREPOINT_DATABASE = "C:\path\to\persistent\carepoint.sqlite3"
py app.py
```

For SMS, set the Twilio variables in each terminal before launching its process (use the real values from the Twilio console locally; do not paste or commit credentials):

```powershell
$env:TWILIO_ACCOUNT_SID = "<Twilio Account SID>"
$env:TWILIO_AUTH_TOKEN = "<Twilio Auth Token>"
$env:TWILIO_FROM_NUMBER = "<Twilio sender number>"
```

In a second terminal, set the same database path and start the worker:

```powershell
$env:CAREPOINT_DATABASE = "C:\path\to\persistent\carepoint.sqlite3"
$env:TWILIO_ACCOUNT_SID = "<Twilio Account SID>"
$env:TWILIO_AUTH_TOKEN = "<Twilio Auth Token>"
$env:TWILIO_FROM_NUMBER = "<Twilio sender number>"
py reminder_worker.py
```

The worker polls every 30 seconds by default, so a reminder may arrive up to about one polling interval after its scheduled time; set `REMINDER_POLL_SECONDS` to a value between 5 and 3600 to change that. `py reminder_worker.py --once` runs a single cycle for a deployment health check. The worker must remain running for dependable scheduled reminders. A browser timer is only used to surface in-app notifications while the dashboard is open; it is not the scheduler. The app does not deduct tablets just because a scheduled time passed: sign in and select **Taken** after taking the medicine to reduce inventory.

Keep `CAREPOINT_SECRET_KEY` stable between application restarts and never use the example value above in deployment. `APP_ENV=production` requires a configured key. `CAREPOINT_DATABASE` must point to storage that persists across restarts and is shared by the web and worker processes. SQLite WAL mode is enabled; use a local persistent volume rather than a network filesystem. Run one web deployment and one worker against that file. Horizontal/multi-host deployments need a database service designed for concurrent network access and an application database migration.

## Deployment

The `Procfile` defines `web` and `worker` process types. Start both in the deployment platform, configure the same persistent database path and environment variables for each, and ensure the worker restarts automatically if it exits. Do not scale the worker to multiple copies; run one reminder worker to avoid concurrent delivery attempts. Set `CAREPOINT_HTTPS=true` behind HTTPS so session cookies are secure. The application is not a replacement for a clinical care system or an emergency notification service.

## Browser and push notifications

The dashboard can show browser notifications while it is open if the user grants notification permission. Permission is requested from Profile & notifications. Browser notifications and service workers require HTTPS, except on localhost for development. Browser support and operating-system settings affect whether these appear.

Closed-browser push requires:

- Public HTTPS deployment (localhost is generally allowed for development).
- The `pywebpush` dependency, already listed in `requirements.txt`.
- A VAPID key pair and `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_SUBJECT` environment variables set on both the web and worker processes. Keep the private key secret. The subject should be a valid `mailto:` or HTTPS contact URI.
- The user to enable browser reminders on each device/browser.

For example, generate a private PEM file and the URL-safe public key using the installed `py_vapid` package:

```python
import base64
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid

vapid = Vapid()
vapid.generate_keys()
vapid.save_key("vapid_private.pem")
public_bytes = vapid.public_key.public_bytes(
    serialization.Encoding.X962,
    serialization.PublicFormat.UncompressedPoint,
)
print(base64.urlsafe_b64encode(public_bytes).decode().rstrip("="))
```

Set `VAPID_PRIVATE_KEY` to the private PEM file path (or secure encoded key material), `VAPID_PUBLIC_KEY` to the printed string, and `VAPID_SUBJECT` to your contact URI. Keep the private PEM out of source control and make it available securely to the worker.

Push subscriptions are device- and browser-specific. Users can revoke permission in browser settings; expired subscriptions are removed when the push service reports that they have expired. Mobile platform behavior varies, and push delivery is not guaranteed.

## SMS

SMS is optional and disabled until a user opts in. Configure `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER` in the server/worker environment; never put credentials in frontend code or commit them. SMS requires a configured Twilio account and sender, adequate credits or a paid plan, and any required account, sender, and destination verification. Trial accounts may only message verified destinations. Availability, registration, and delivery rules vary by country and carrier.

The opted-in SMS channel is used for scheduled-dose reminders and low-stock alerts, including the urgent two-tablet alert and the out-of-stock alert. The dashboard records whether Twilio accepted or rejected a request. An accepted API request (and its message SID) does **not** prove the carrier delivered the message. If credentials are missing, the delivery is recorded as failed and retried with a bounded backoff; the app does not report it as sent.

## Inventory behavior

Each medicine has a digital tablet inventory (maximum 1,000 tablets per medicine). Marking a scheduled dose Taken deducts the configured tablets-per-dose once, transactionally. Repeating the same action does not deduct again; Skipped and Snoozed do not change stock. Individual tablet buttons provide an explicit, auditable manual correction. A reminder notification alone never consumes inventory.

Low-stock notifications are created when the configured threshold state changes; two or fewer tablets is urgent and zero is out of stock. For SMS, enable the registered-mobile option in Profile & notifications. The run-out estimate uses the currently enabled weekly schedule and medicine end date and is an estimate, not an instruction to change treatment.

## Tests

Run the focused account, ownership, schedule, notification, and inventory tests with:

```powershell
py -m unittest test_reminders -v
```

The tests use a temporary SQLite database and do not change the application's saved medicine catalog or production database.
