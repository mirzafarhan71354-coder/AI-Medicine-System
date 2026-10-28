from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reminder_db import SCHEMA, connect_database


MAX_MEDICINES_PER_SUBMISSION = 10
MAX_TABLETS_PER_MEDICINE = 1000
MAX_SNOOZE_MINUTES = 60


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_worker_database():
    database_file = Path(os.environ.get(
        "CAREPOINT_DATABASE",
        Path(__file__).resolve().parent / "data" / "carepoint.sqlite3",
    ))
    database_file.parent.mkdir(parents=True, exist_ok=True)
    return connect_database(database_file)


def validate_timezone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        return None


def materialize_scheduled_doses(connection, user_id, days=1, start_date=None):
    if days < 1 or days > 14:
        raise ValueError("Dose generation range must be between 1 and 14 days.")

    schedules = connection.execute(
        """
        SELECT s.id, s.medicine_id, s.time_local, s.days_of_week,
               s.enabled, m.start_date, m.end_date, u.timezone
        FROM medicine_schedules AS s
        JOIN medicines AS m ON m.id = s.medicine_id AND m.user_id = s.user_id
        JOIN users AS u ON u.id = s.user_id
        WHERE s.user_id = ? AND s.enabled = 1
        """,
        (user_id,),
    ).fetchall()

    today = start_date or date.today()
    created = 0
    for schedule in schedules:
        zone = validate_timezone(schedule["timezone"])
        if zone is None:
            continue
        weekdays = {int(day) for day in schedule["days_of_week"].split(",")}
        scheduled_time = time.fromisoformat(schedule["time_local"])
        start = date.fromisoformat(schedule["start_date"])
        end = date.fromisoformat(schedule["end_date"])

        for offset in range(days):
            scheduled_date = today + timedelta(days=offset)
            if scheduled_date < start or scheduled_date > end:
                continue
            if scheduled_date.weekday() not in weekdays:
                continue
            scheduled_for = datetime.combine(
                scheduled_date, scheduled_time, tzinfo=zone
            ).isoformat(timespec="seconds")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO dose_events
                    (user_id, medicine_id, schedule_id, scheduled_for)
                VALUES (?, ?, ?, ?)
                """,
                (
                    user_id, schedule["medicine_id"], schedule["id"],
                    scheduled_for,
                ),
            )
            created += cursor.rowcount

    return created


def _estimate_days_remaining(connection, medicine, remaining, user_timezone):
    if remaining <= 0:
        return 0
    schedules = connection.execute(
        """
        SELECT time_local, days_of_week FROM medicine_schedules
        WHERE medicine_id = ? AND enabled = 1
        """,
        (medicine["id"],),
    ).fetchall()
    if not schedules:
        return None

    zone = validate_timezone(user_timezone)
    if zone is None:
        return None
    start = max(
        datetime.now(zone).date(),
        date.fromisoformat(medicine["start_date"]),
    )
    end = date.fromisoformat(medicine["end_date"])
    if start > end:
        return None

    doses_remaining = 0
    for offset in range(min((end - start).days + 1, 3660)):
        scheduled_date = start + timedelta(days=offset)
        for schedule in schedules:
            weekdays = {int(day) for day in schedule["days_of_week"].split(",")}
            if scheduled_date.weekday() in weekdays:
                doses_remaining += medicine["tablets_per_dose"]
                if doses_remaining >= remaining:
                    return offset
    return None


def get_medicine_inventory(connection, user_id, user_timezone):
    medicines = connection.execute(
        """
        SELECT m.*,
               COUNT(t.id) AS total_capacity,
               COALESCE(SUM(CASE WHEN t.consumed_at IS NULL THEN 1 ELSE 0 END), 0)
                   AS tablets_remaining,
               COALESCE(SUM(CASE WHEN t.consumed_at IS NOT NULL THEN 1 ELSE 0 END), 0)
                   AS tablets_consumed
        FROM medicines AS m
        LEFT JOIN medicine_tablets AS t ON t.medicine_id = m.id
        WHERE m.user_id = ?
        GROUP BY m.id
        ORDER BY m.name COLLATE NOCASE, m.id
        """,
        (user_id,),
    ).fetchall()

    results = []
    for row in medicines:
        medicine = dict(row)
        zone = validate_timezone(user_timezone)
        medicine["strips_remaining"] = (
            (medicine["tablets_remaining"] + medicine["tablets_per_strip"] - 1)
            // medicine["tablets_per_strip"]
        )
        medicine["estimated_days_remaining"] = _estimate_days_remaining(
            connection,
            medicine,
            medicine["tablets_remaining"],
            user_timezone,
        )
        if medicine["estimated_days_remaining"] is not None and zone is not None:
            estimate_start = max(
                datetime.now(zone).date(),
                date.fromisoformat(medicine["start_date"]),
            )
            medicine["estimated_runout_date"] = (
                estimate_start
                + timedelta(days=medicine["estimated_days_remaining"])
            ).isoformat()
        else:
            medicine["estimated_runout_date"] = None
        remaining = medicine["tablets_remaining"]
        threshold = medicine["low_stock_threshold"]
        medicine["stock_status"] = (
            "out" if remaining == 0
            else "urgent" if remaining <= 2
            else "low" if remaining <= threshold
            else "ok"
        )
        medicine["tablet_rows"] = connection.execute(
            """
            SELECT id, tablet_number, consumed_at
            FROM medicine_tablets
            WHERE medicine_id = ?
            ORDER BY tablet_number
            """,
            (medicine["id"],),
        ).fetchall()
        medicine["schedules"] = connection.execute(
            """
            SELECT id, time_local, days_of_week, enabled
            FROM medicine_schedules
            WHERE medicine_id = ?
            ORDER BY time_local
            """,
            (medicine["id"],),
        ).fetchall()
        results.append(medicine)
    return results


def get_dose_schedule(connection, user_id, user_timezone):
    zone = validate_timezone(user_timezone)
    if zone is None:
        raise ValueError("The saved timezone is invalid. Update it in your profile.")
    today = datetime.now(zone).date()
    materialize_scheduled_doses(
        connection, user_id, days=7, start_date=today
    )
    connection.commit()
    start = today.isoformat()
    end = (today + timedelta(days=7)).isoformat()
    rows = connection.execute(
        """
        SELECT d.id, d.scheduled_for, d.status, d.snoozed_until,
               d.action_at, d.notification_round,
               m.id AS medicine_id, m.name AS medicine_name,
               m.instructions, m.tablets_per_dose,
               s.enabled AS schedule_enabled
        FROM dose_events AS d
        JOIN medicines AS m ON m.id = d.medicine_id AND m.user_id = d.user_id
        JOIN medicine_schedules AS s
          ON s.id = d.schedule_id AND s.user_id = d.user_id
        WHERE d.user_id = ?
          AND substr(d.scheduled_for, 1, 10) >= ?
          AND substr(d.scheduled_for, 1, 10) < ?
          AND (s.enabled = 1 OR d.status IN ('taken', 'skipped'))
          AND substr(d.scheduled_for, 1, 10) BETWEEN m.start_date AND m.end_date
        ORDER BY d.scheduled_for, m.name COLLATE NOCASE
        """,
        (user_id, start, end),
    ).fetchall()
    return [dict(row) for row in rows]


def apply_dose_action(connection, user_id, dose_id, action, snooze_minutes=10):
    if action not in {"taken", "skipped", "snoozed"}:
        return False, "Choose Taken, Skipped, or Snoozed."
    if action == "snoozed" and not 1 <= snooze_minutes <= MAX_SNOOZE_MINUTES:
        return False, "Snooze duration must be between 1 and 60 minutes."

    connection.execute("BEGIN IMMEDIATE")
    dose = connection.execute(
        """
        SELECT d.*, m.tablets_per_dose
        FROM dose_events AS d
        JOIN medicines AS m ON m.id = d.medicine_id AND m.user_id = d.user_id
        WHERE d.id = ? AND d.user_id = ?
        """,
        (dose_id, user_id),
    ).fetchone()
    if dose is None:
        connection.rollback()
        return False, "Dose not found."
    if dose["status"] == "taken":
        connection.commit()
        return True, "This dose was already recorded as taken; inventory was not changed."

    now = utc_timestamp()
    if action == "taken":
        inventory = connection.execute(
            """
            SELECT id FROM medicine_tablets
            WHERE medicine_id = ? AND consumed_at IS NULL
            ORDER BY tablet_number
            LIMIT ?
            """,
            (dose["medicine_id"], dose["tablets_per_dose"]),
        ).fetchall()
        if len(inventory) < dose["tablets_per_dose"]:
            connection.rollback()
            return False, "Not enough tablets are available to record this dose."

        connection.execute(
            """
            INSERT INTO inventory_events
                (user_id, medicine_id, dose_id, event_type, quantity, created_at)
            VALUES (?, ?, ?, 'dose_taken', ?, ?)
            """,
            (
                user_id, dose["medicine_id"], dose_id,
                -dose["tablets_per_dose"], now,
            ),
        )
        connection.executemany(
            """
            UPDATE medicine_tablets
            SET consumed_at = ?, consumption_source = 'dose', dose_id = ?
            WHERE id = ? AND consumed_at IS NULL
            """,
            ((now, dose_id, tablet["id"]) for tablet in inventory),
        )
        connection.execute(
            """
            UPDATE dose_events
            SET status = 'taken', snoozed_until = NULL, action_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (now, dose_id, user_id),
        )
        connection.commit()
        return True, "Dose recorded as taken and inventory updated."

    if action == "skipped":
        connection.execute(
            """
            UPDATE dose_events
            SET status = 'skipped', snoozed_until = NULL, action_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (now, dose_id, user_id),
        )
        connection.commit()
        return True, "Dose recorded as skipped; inventory was not changed."

    snoozed_until = (
        datetime.now(timezone.utc) + timedelta(minutes=snooze_minutes)
    ).isoformat(timespec="seconds")
    connection.execute(
        """
        UPDATE dose_events
        SET status = 'snoozed', snoozed_until = ?, action_at = ?,
            notification_round = notification_round + 1
        WHERE id = ? AND user_id = ?
        """,
        (snoozed_until, now, dose_id, user_id),
    )
    connection.commit()
    return True, f"Reminder snoozed for {snooze_minutes} minutes."


def create_notification(connection, user_id, medicine_id, dose_id, event_type,
                        dedupe_key, message):
    connection.execute(
        """
        INSERT OR IGNORE INTO notification_history
            (user_id, medicine_id, dose_id, event_type, dedupe_key, message)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, medicine_id, dose_id, event_type, dedupe_key, message),
    )
    return connection.execute(
        "SELECT id FROM notification_history WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()["id"]


def _record_delivery(connection, notification_id, channel, attempt, status,
                     provider_reference=None, error_message=None):
    connection.execute(
        """
        INSERT OR IGNORE INTO notification_deliveries
            (notification_id, channel, attempt, status,
             provider_reference, error_message)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            notification_id, channel, attempt, status,
            provider_reference, error_message,
        ),
    )


def _send_sms(user, message):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    sender = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
    if not (account_sid and auth_token and sender):
        return None, "SMS is enabled for this account but Twilio credentials are not configured."

    import requests

    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            data={"To": user["mobile"], "From": sender, "Body": message[:1500]},
            auth=(account_sid, auth_token),
            timeout=15,
        )
        if not response.ok:
            return None, f"Twilio HTTP {response.status_code}: {response.text[:300]}"
        payload = response.json()
        return payload.get("sid"), None
    except (requests.RequestException, ValueError) as error:
        return None, str(error)[:300]


def _send_push(subscription, message, notification_id):
    public_key = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    private_key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    subject = os.environ.get("VAPID_SUBJECT", "").strip()
    if not (public_key and private_key and subject):
        return None, "Web Push VAPID credentials are not configured."

    from urllib.parse import urlparse

    host = (urlparse(subscription["endpoint"]).hostname or "").lower()
    approved_suffixes = (
        ".push.services.mozilla.com",
        ".fcm.googleapis.com",
        ".push.apple.com",
        ".notify.windows.com",
    )
    if not any(
        host == suffix[1:] or host.endswith(suffix)
        for suffix in approved_suffixes
    ):
        return None, "Push endpoint host is not supported."

    import requests

    try:
        from pywebpush import WebPushException, webpush

        response = webpush(
            subscription_info=json.loads(subscription["subscription_json"]),
            data=json.dumps({
                "title": "Medicine reminder",
                "body": message,
                "url": "/dashboard",
                "tag": f"carepoint-{notification_id}",
            }),
            vapid_private_key=private_key,
            vapid_claims={"sub": subject},
            timeout=15,
        )
        return str(response.status_code), None
    except ImportError as error:
        return None, f"Web Push support is unavailable: {error}"
    except WebPushException as error:
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        if status_code == 410:
            return None, "Push subscription expired."
        return None, str(error)[:300]
    except requests.RequestException as error:
        return None, str(error)[:300]
    except (ValueError, TypeError, OSError) as error:
        return None, str(error)[:300]


def _deliver_notification(connection, user, notification_id, message):
    channels = []
    if user["sms_enabled"]:
        channels.append(("sms", None))
    subscriptions = connection.execute(
        """
        SELECT id, endpoint, subscription_json
        FROM push_subscriptions WHERE user_id = ?
        """,
        (user["id"],),
    ).fetchall()
    channels.extend(("push", subscription) for subscription in subscriptions)

    for channel, subscription in channels:
        latest = connection.execute(
            """
            SELECT attempt, status, created_at
            FROM notification_deliveries
            WHERE notification_id = ? AND channel = ?
            ORDER BY attempt DESC LIMIT 1
            """,
            (notification_id, channel),
        ).fetchone()
        if latest is not None:
            if latest["status"] == "accepted":
                continue
            try:
                last_attempt = datetime.fromisoformat(latest["created_at"]).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                last_attempt = datetime.now(timezone.utc)
            if (
                latest["attempt"] >= 3
                or datetime.now(timezone.utc) - last_attempt < timedelta(minutes=5)
            ):
                continue
            attempt = latest["attempt"] + 1
        else:
            attempt = 1

        if channel == "sms":
            reference, error = _send_sms(user, message)
        else:
            reference, error = _send_push(subscription, message, notification_id)
            if error and "expired" in error.lower():
                connection.execute(
                    "DELETE FROM push_subscriptions WHERE id = ?",
                    (subscription["id"],),
                )
        _record_delivery(
            connection,
            notification_id,
            channel,
            attempt,
            "accepted" if reference else "failed",
            reference,
            error,
        )
        connection.commit()


def process_notification_cycle(database_path=None, now=None):
    connection = connect_database(database_path)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    try:
        users = connection.execute(
            "SELECT id, timezone FROM users"
        ).fetchall()
        for user in users:
            zone = validate_timezone(user["timezone"])
            if zone is not None:
                materialize_scheduled_doses(
                    connection,
                    user["id"],
                    days=1,
                    start_date=current.astimezone(zone).date(),
                )
        connection.commit()

        pending = connection.execute(
            """
            SELECT d.id, d.user_id, d.medicine_id, d.scheduled_for,
                   d.status, d.snoozed_until, d.notification_round,
                   m.name AS medicine_name, m.instructions,
                   m.start_date AS medicine_start, m.end_date AS medicine_end,
                   s.enabled AS schedule_enabled,
                   u.name AS user_name, u.mobile, u.sms_enabled,
                   u.timezone
            FROM dose_events AS d
            JOIN medicines AS m ON m.id = d.medicine_id AND m.user_id = d.user_id
            JOIN medicine_schedules AS s
              ON s.id = d.schedule_id AND s.user_id = d.user_id
            JOIN users AS u ON u.id = d.user_id
            WHERE d.status IN ('pending', 'snoozed') AND s.enabled = 1
            """
        ).fetchall()
        notifications_created = 0
        for dose in pending:
            zone = validate_timezone(dose["timezone"])
            if zone is None:
                continue
            scheduled_for = datetime.fromisoformat(dose["scheduled_for"])
            local_now = current.astimezone(zone)
            scheduled_date = scheduled_for.date()
            if (
                scheduled_for > local_now
                or scheduled_date != local_now.date()
                or scheduled_date < date.fromisoformat(dose["medicine_start"])
                or scheduled_date > date.fromisoformat(dose["medicine_end"])
            ):
                continue
            if dose["snoozed_until"]:
                snoozed_until = datetime.fromisoformat(dose["snoozed_until"])
                if snoozed_until > current:
                    continue

            notification_key = (
                f"dose:{dose['id']}:round:{dose['notification_round']}"
            )
            message = (
                f"It's time to take {dose['medicine_name']} "
                f"({scheduled_for.strftime('%I:%M %p').lstrip('0')}). "
                f"Dosage instructions: {dose['instructions']}"
            )
            connection.execute("BEGIN IMMEDIATE")
            notification_id = create_notification(
                connection,
                dose["user_id"],
                dose["medicine_id"],
                dose["id"],
                "dose_reminder",
                notification_key,
                message,
            )
            connection.commit()
            if connection.execute(
                """
                SELECT 1 FROM notification_deliveries
                WHERE notification_id = ? AND channel = 'in_app'
                """,
                (notification_id,),
            ).fetchone() is None:
                _record_delivery(
                    connection, notification_id, "in_app", 1, "accepted"
                )
                connection.commit()
            user = {
                "id": dose["user_id"],
                "mobile": dose["mobile"],
                "sms_enabled": dose["sms_enabled"],
            }
            _deliver_notification(connection, user, notification_id, message)
            notifications_created += 1

        notifications_created += _process_low_stock(connection)
        notifications_created += _retry_failed_notifications(connection)
        return notifications_created
    finally:
        connection.close()


def _process_low_stock(connection):
    rows = connection.execute(
        """
        SELECT m.id, m.user_id, m.name, m.low_stock_threshold, m.alert_state,
               u.name AS user_name, u.mobile, u.sms_enabled,
               COUNT(t.id) AS total_capacity,
               COALESCE(SUM(CASE WHEN t.consumed_at IS NULL THEN 1 ELSE 0 END), 0)
                   AS tablets_remaining
        FROM medicines AS m
        JOIN users AS u ON u.id = m.user_id
        LEFT JOIN medicine_tablets AS t ON t.medicine_id = m.id
        GROUP BY m.id
        """
    ).fetchall()
    created = 0
    for medicine in rows:
        remaining = medicine["tablets_remaining"]
        state = (
            "out" if remaining == 0
            else "urgent" if remaining <= 2
            else "low" if remaining <= medicine["low_stock_threshold"]
            else "ok"
        )
        if state == medicine["alert_state"] and remaining != 1:
            continue
        connection.execute(
            "UPDATE medicines SET alert_state = ? WHERE id = ? AND user_id = ?",
            (state, medicine["id"], medicine["user_id"]),
        )
        if state == "ok":
            continue
        descriptions = {
            "low": f"Low stock: {remaining} {medicine['name']} tablets remain.",
            "urgent": (
                f"Low stock: only 2 {medicine['name']} tablets remain."
                if remaining == 2
                else f"Urgent: only {remaining} {medicine['name']} tablet(s) remain."
            ),
            "out": f"Out of stock: no {medicine['name']} tablets remain.",
        }
        message = descriptions[state]
        notification_id = create_notification(
            connection,
            medicine["user_id"],
            medicine["id"],
            None,
            f"low_stock_{state}",
            f"stock:{medicine['id']}:{state}:{remaining}",
            message,
        )
        connection.commit()
        if connection.execute(
            """
            SELECT 1 FROM notification_deliveries
            WHERE notification_id = ? AND channel = 'in_app'
            """,
            (notification_id,),
        ).fetchone() is None:
            _record_delivery(
                connection, notification_id, "in_app", 1, "accepted"
            )
        user = {
            "id": medicine["user_id"],
            "mobile": medicine["mobile"],
            "sms_enabled": medicine["sms_enabled"],
        }
        _deliver_notification(connection, user, notification_id, message)
        connection.commit()
        created += 1
    return created


def _retry_failed_notifications(connection):
    failed = connection.execute(
        """
        SELECT DISTINCT n.id, n.user_id, n.message,
               u.mobile, u.sms_enabled
        FROM notification_history AS n
        JOIN users AS u ON u.id = n.user_id
        JOIN notification_deliveries AS d ON d.notification_id = n.id
        WHERE d.status = 'failed'
        """
    ).fetchall()
    retried = 0
    for notification in failed:
        _deliver_notification(
            connection,
            {
                "id": notification["user_id"],
                "mobile": notification["mobile"],
                "sms_enabled": notification["sms_enabled"],
            },
            notification["id"],
            notification["message"],
        )
        retried += 1
    return retried
