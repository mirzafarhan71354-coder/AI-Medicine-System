from datetime import datetime, time, timedelta
from flask import Flask
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from account_routes import MAX_TABLETS_PER_MEDICINE, bp as accounts_blueprint
from reminder_db import connect_database, init_database
from reminder_service import apply_dose_action, process_notification_cycle


class ReminderWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "carepoint.sqlite3"
        project_root = Path(__file__).resolve().parent
        self.app = Flask(
            __name__,
            template_folder=str(project_root / "templates"),
            static_folder=str(project_root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            CAREPOINT_DATABASE=str(self.database_path),
            SECRET_KEY="test-secret-key",
            VAPID_PUBLIC_KEY="",
            VAPID_PRIVATE_KEY="",
            VAPID_SUBJECT="",
            TWILIO_ACCOUNT_SID="",
            TWILIO_AUTH_TOKEN="",
            TWILIO_FROM_NUMBER="",
        )
        self.app.add_url_rule("/", endpoint="home", view_func=lambda: "CarePoint")
        self.app.add_url_rule("/tools", endpoint="tools", view_func=lambda: "Health tools")
        self.app.add_url_rule(
            "/service-worker.js",
            endpoint="service_worker",
            view_func=lambda: "",
        )
        init_database(self.app)
        self.app.register_blueprint(accounts_blueprint)
        self.client = self.app.test_client()
        self.register(self.client, "owner@example.test", "+919876543210")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def csrf_token(self, client):
        response = client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            response.get_data(as_text=True),
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def register(self, client, email, mobile):
        response = client.get("/register")
        self.assertEqual(response.status_code, 200)
        token = re.search(
            r'name="csrf_token" value="([^"]+)"',
            response.get_data(as_text=True),
        ).group(1)
        return client.post(
            "/register",
            data={
                "csrf_token": token,
                "name": "CarePoint Test User",
                "email": email,
                "mobile": mobile,
                "password": "a-secure-test-password",
                "confirm_password": "a-secure-test-password",
            },
            follow_redirects=True,
        )

    def add_medicines(self, client, names):
        token = self.csrf_token(client)
        user_date = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        return client.post(
            "/dashboard/medicines/add",
            data={
                "csrf_token": token,
                "medicine_names": "\n".join(names),
                "instructions": "Take with water",
                "start_date": user_date.isoformat(),
                "end_date": (user_date + timedelta(days=30)).isoformat(),
                "tablets_per_strip": "10",
                "strips": "1",
                "tablets_per_dose": "1",
                "low_stock_threshold": "5",
                "reminder_times": ["10:59"],
                "days_of_week": [str(user_date.weekday())],
            },
            follow_redirects=True,
        )

    def test_registration_dashboard_and_multiple_medicines(self):
        response = self.add_medicines(
            self.client, ["Medicine Alpha", "Medicine Beta"]
        )
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Medicine Alpha", page)
        self.assertIn("Medicine Beta", page)

        connection = connect_database(self.database_path)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM medicines").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM medicine_schedules"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM medicine_tablets"
                ).fetchone()[0],
                20,
            )
        finally:
            connection.close()

    def test_add_medicine_does_not_default_to_morning_reminder(self):
        response = self.client.get("/dashboard")
        page = response.get_data(as_text=True)
        self.assertNotIn('name="reminder_times" value="08:00"', page)
        self.assertIn("Empty time fields are ignored.", page)

        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        response = self.client.post(
            "/dashboard/medicines/add",
            data={
                "csrf_token": self.csrf_token(self.client),
                "medicine_names": "No-time medicine",
                "instructions": "Take with water",
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=30)).isoformat(),
                "tablets_per_strip": "10",
                "strips": "1",
                "tablets_per_dose": "1",
                "low_stock_threshold": "5",
                "reminder_times": ["", "", "", ""],
                "days_of_week": [str(today.weekday())],
            },
            follow_redirects=True,
        )
        self.assertIn(
            "Choose at least one reminder time before saving the medicine.",
            response.get_data(as_text=True),
        )
        connection = connect_database(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM medicines"
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_take_is_idempotent_and_skip_does_not_consume(self):
        self.add_medicines(self.client, ["Dose test medicine"])
        connection = connect_database(self.database_path)
        try:
            user_id = connection.execute("SELECT id FROM users").fetchone()["id"]
            medicine = connection.execute(
                "SELECT id FROM medicines WHERE user_id = ?", (user_id,)
            ).fetchone()
            dose_id = connection.execute(
                "SELECT id FROM dose_events WHERE medicine_id = ?",
                (medicine["id"],),
            ).fetchone()["id"]

            success, _ = apply_dose_action(
                connection, user_id, dose_id, "skipped"
            )
            self.assertTrue(success)
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM medicine_tablets
                    WHERE medicine_id = ? AND consumed_at IS NULL
                    """,
                    (medicine["id"],),
                ).fetchone()[0],
                10,
            )
            success, _ = apply_dose_action(
                connection, user_id, dose_id, "snoozed", snooze_minutes=10
            )
            self.assertTrue(success)
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM medicine_tablets
                    WHERE medicine_id = ? AND consumed_at IS NULL
                    """,
                    (medicine["id"],),
                ).fetchone()[0],
                10,
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT action_at FROM dose_events WHERE id = ?",
                    (dose_id,),
                ).fetchone()["action_at"]
            )
            success, _ = apply_dose_action(
                connection, user_id, dose_id, "taken"
            )
            self.assertTrue(success)
            success, message = apply_dose_action(
                connection, user_id, dose_id, "taken"
            )
            self.assertTrue(success)
            self.assertIn("already recorded", message)
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM medicine_tablets
                    WHERE medicine_id = ? AND consumed_at IS NULL
                    """,
                    (medicine["id"],),
                ).fetchone()[0],
                9,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM inventory_events WHERE dose_id = ?",
                    (dose_id,),
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()
        connection = connect_database(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM medicine_tablets
                    WHERE medicine_id = ? AND consumed_at IS NULL
                    """,
                    (medicine["id"],),
                ).fetchone()[0],
                9,
            )
        finally:
            connection.close()

    def test_worker_creates_one_due_reminder(self):
        self.add_medicines(self.client, ["Due medicine"])
        connection = connect_database(self.database_path)
        try:
            connection.execute("UPDATE users SET sms_enabled = 1")
            connection.commit()
        finally:
            connection.close()
        local_today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        now = datetime.combine(
            local_today, time(11, 1), ZoneInfo("Asia/Kolkata")
        ).astimezone(ZoneInfo("UTC"))

        with patch(
            "reminder_service._send_sms",
            return_value=("SM-test-reference", None),
        ) as send_sms:
            process_notification_cycle(self.database_path, now=now)
            process_notification_cycle(self.database_path, now=now)
            send_sms.assert_called_once()
            self.assertIn("Due medicine", send_sms.call_args.args[1])
            self.assertIn("Take with water", send_sms.call_args.args[1])
            self.assertIn("10:59 AM", send_sms.call_args.args[1])

        connection = connect_database(self.database_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM notification_history WHERE event_type = 'dose_reminder'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM notification_deliveries
                    WHERE channel = 'in_app' AND status = 'accepted'
                    """
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_other_user_cannot_correct_tablet(self):
        self.add_medicines(self.client, ["Private medicine"])
        connection = connect_database(self.database_path)
        try:
            tablet_id = connection.execute(
                "SELECT id FROM medicine_tablets ORDER BY id LIMIT 1"
            ).fetchone()["id"]
        finally:
            connection.close()

        other_client = self.app.test_client()
        registration = self.register(
            other_client, "other@example.test", "+14155552671"
        )
        self.assertEqual(registration.status_code, 200)
        response = other_client.post(
            f"/dashboard/tablets/{tablet_id}",
            data={"csrf_token": self.csrf_token(other_client), "state": "consumed"},
        )
        self.assertEqual(response.status_code, 404)

        connection = connect_database(self.database_path)
        try:
            self.assertIsNone(
                connection.execute(
                    "SELECT consumed_at FROM medicine_tablets WHERE id = ?",
                    (tablet_id,),
                ).fetchone()["consumed_at"]
            )
        finally:
            connection.close()

    def test_schedule_edit_pause_resume_and_delete(self):
        self.add_medicines(self.client, ["Schedule medicine"])
        token = self.csrf_token(self.client)
        response = self.client.post(
            "/dashboard/medicines/1/schedules/add",
            data={
                "csrf_token": token,
                "time_local": "14:00",
                "days_of_week": ["0", "2", "4"],
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        connection = connect_database(self.database_path)
        try:
            schedules = connection.execute(
                "SELECT id, time_local FROM medicine_schedules ORDER BY id"
            ).fetchall()
            self.assertEqual(len(schedules), 2)
            second_schedule_id = schedules[1]["id"]
        finally:
            connection.close()

        response = self.client.post(
            f"/dashboard/schedules/{second_schedule_id}/edit",
            data={
                "csrf_token": self.csrf_token(self.client),
                "time_local": "15:30",
                "days_of_week": ["1", "3", "5"],
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            f"/dashboard/schedules/{second_schedule_id}/toggle",
            data={
                "csrf_token": self.csrf_token(self.client),
                "enabled": "0",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        connection = connect_database(self.database_path)
        try:
            schedule = connection.execute(
                "SELECT time_local, enabled FROM medicine_schedules WHERE id = ?",
                (second_schedule_id,),
            ).fetchone()
            self.assertEqual(schedule["time_local"], "15:30")
            self.assertEqual(schedule["enabled"], 0)
        finally:
            connection.close()

        self.client.post(
            f"/dashboard/schedules/{second_schedule_id}/toggle",
            data={
                "csrf_token": self.csrf_token(self.client),
                "enabled": "1",
            },
        )
        self.client.post(
            f"/dashboard/schedules/{second_schedule_id}/delete",
            data={"csrf_token": self.csrf_token(self.client)},
        )
        connection = connect_database(self.database_path)
        try:
            self.assertIsNone(
                connection.execute(
                    "SELECT id FROM medicine_schedules WHERE id = ?",
                    (second_schedule_id,),
                ).fetchone()
            )
        finally:
            connection.close()

    def test_manual_tablet_correction_is_persisted(self):
        self.add_medicines(self.client, ["Correction medicine"])
        connection = connect_database(self.database_path)
        try:
            tablet_id = connection.execute(
                "SELECT id FROM medicine_tablets ORDER BY id LIMIT 1"
            ).fetchone()["id"]
        finally:
            connection.close()

        self.client.post(
            f"/dashboard/tablets/{tablet_id}",
            data={
                "csrf_token": self.csrf_token(self.client),
                "state": "consumed",
            },
        )
        connection = connect_database(self.database_path)
        try:
            self.assertIsNotNone(
                connection.execute(
                    "SELECT consumed_at FROM medicine_tablets WHERE id = ?",
                    (tablet_id,),
                ).fetchone()["consumed_at"]
            )
        finally:
            connection.close()

        self.client.post(
            f"/dashboard/tablets/{tablet_id}",
            data={
                "csrf_token": self.csrf_token(self.client),
                "state": "available",
            },
        )
        connection = connect_database(self.database_path)
        try:
            tablet = connection.execute(
                """
                SELECT consumed_at, dose_id FROM medicine_tablets WHERE id = ?
                """,
                (tablet_id,),
            ).fetchone()
            self.assertIsNone(tablet["consumed_at"])
            self.assertIsNone(tablet["dose_id"])
        finally:
            connection.close()

    def test_dashboard_hides_upcoming_and_offers_medicine_actions(self):
        self.add_medicines(self.client, ["Empty medicine"])
        connection = connect_database(self.database_path)
        try:
            medicine_id = connection.execute(
                "SELECT id FROM medicines"
            ).fetchone()["id"]
            connection.execute(
                """
                UPDATE medicine_tablets
                SET consumed_at = CURRENT_TIMESTAMP,
                    consumption_source = 'manual'
                WHERE medicine_id = ?
                """,
                (medicine_id,),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.get("/dashboard")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("NEXT SEVEN DAYS", page)
        self.assertNotIn("Upcoming doses", page)
        self.assertIn(f'data-open-details="medicine-management-{medicine_id}"', page)
        self.assertIn("Delete empty medicine", page)

    def test_login_logout_and_saved_medicines(self):
        self.add_medicines(self.client, ["Saved medicine"])
        token = self.csrf_token(self.client)
        logout = self.client.post(
            "/logout", data={"csrf_token": token}, follow_redirects=True
        )
        self.assertEqual(logout.status_code, 200)
        protected = self.client.get("/dashboard")
        self.assertEqual(protected.status_code, 302)

        login_page = self.client.get("/login")
        token = re.search(
            r'name="csrf_token" value="([^"]+)"',
            login_page.get_data(as_text=True),
        ).group(1)
        response = self.client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "owner@example.test",
                "password": "a-secure-test-password",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(
            "Saved medicine",
            dashboard.get_data(as_text=True),
        )
        self.assertEqual(self.client.get("/profile").status_code, 200)

    def test_profile_explains_missing_sms_setup_and_provider_failures(self):
        profile = self.client.get("/profile")
        page = profile.get_data(as_text=True)
        self.assertEqual(profile.status_code, 200)
        self.assertIn("SMS setup: not configured on this app server", page)
        self.assertIn("SMS opt-in is off", page)
        self.assertIn("No SMS delivery attempt is recorded", page)

        connection = connect_database(self.database_path)
        try:
            user_id = connection.execute(
                "SELECT id FROM users"
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO notification_history
                    (user_id, event_type, dedupe_key, message)
                VALUES (?, 'dose_reminder', 'profile-sms-test', 'Test dose reminder')
                """,
                (user_id,),
            )
            notification_id = connection.execute(
                """
                SELECT id FROM notification_history
                WHERE dedupe_key = 'profile-sms-test'
                """
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO notification_deliveries
                    (notification_id, channel, attempt, status, error_message)
                VALUES (?, 'sms', 1, 'failed', 'Twilio credentials are not configured')
                """,
                (notification_id,),
            )
            connection.commit()
        finally:
            connection.close()

        profile = self.client.get("/profile")
        page = profile.get_data(as_text=True)
        self.assertIn("Latest SMS attempt: Failed", page)
        self.assertIn("Twilio credentials are not configured", page)

    def test_low_stock_alert_and_refill(self):
        self.add_medicines(self.client, ["Low stock medicine"])
        connection = connect_database(self.database_path)
        try:
            medicine = connection.execute(
                "SELECT id FROM medicines"
            ).fetchone()
            connection.execute(
                """
                UPDATE medicine_tablets
                SET consumed_at = CURRENT_TIMESTAMP,
                    consumption_source = 'manual'
                WHERE medicine_id = ? AND tablet_number <= 5
                """,
                (medicine["id"],),
            )
            connection.commit()
            medicine_id = medicine["id"]
            connection.execute("UPDATE users SET sms_enabled = 1")
            connection.commit()
        finally:
            connection.close()

        with patch(
            "reminder_service._send_sms",
            return_value=("SM-test-reference", None),
        ) as send_sms:
            process_notification_cycle(
                self.database_path, now=datetime.now(ZoneInfo("UTC"))
            )
            connection = connect_database(self.database_path)
            try:
                connection.execute(
                    """
                    UPDATE medicine_tablets
                    SET consumed_at = CURRENT_TIMESTAMP,
                        consumption_source = 'manual'
                    WHERE medicine_id = ? AND tablet_number BETWEEN 6 AND 8
                    """,
                    (medicine_id,),
                )
                connection.commit()
            finally:
                connection.close()
            process_notification_cycle(
                self.database_path, now=datetime.now(ZoneInfo("UTC"))
            )
            connection = connect_database(self.database_path)
            try:
                at_two = connection.execute(
                    """
                    SELECT message FROM notification_history
                    WHERE medicine_id = ? AND dedupe_key = ?
                    """,
                    (medicine_id, f"stock:{medicine_id}:urgent:2"),
                ).fetchone()
                self.assertIsNotNone(at_two)
                self.assertIn("only 2", at_two["message"])
                connection.execute(
                    """
                    UPDATE medicine_tablets
                    SET consumed_at = CURRENT_TIMESTAMP,
                        consumption_source = 'manual'
                    WHERE medicine_id = ? AND tablet_number BETWEEN 9 AND 10
                    """,
                    (medicine_id,),
                )
                connection.commit()
            finally:
                connection.close()
            process_notification_cycle(
                self.database_path, now=datetime.now(ZoneInfo("UTC"))
            )
            sms_messages = [
                call.args[1] for call in send_sms.call_args_list
            ]
            self.assertTrue(
                any("only 2" in message for message in sms_messages)
            )
            self.assertTrue(
                any("Out of stock" in message for message in sms_messages)
            )
        process_notification_cycle(
            self.database_path, now=datetime.now(ZoneInfo("UTC"))
        )
        connection = connect_database(self.database_path)
        try:
            low_alert_count = connection.execute(
                """
                SELECT COUNT(*) FROM notification_history
                WHERE medicine_id = ? AND event_type = 'low_stock_low'
                """,
                (medicine_id,),
            ).fetchone()[0]
            out_of_stock = connection.execute(
                """
                SELECT message FROM notification_history
                WHERE medicine_id = ? AND dedupe_key = ?
                """,
                (medicine_id, f"stock:{medicine_id}:out:0"),
            ).fetchone()
            self.assertEqual(low_alert_count, 1)
            self.assertIsNotNone(out_of_stock)
            self.assertIn("Out of stock", out_of_stock["message"])
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM notification_history
                    WHERE medicine_id = ? AND dedupe_key = ?
                    """,
                    (medicine_id, f"stock:{medicine_id}:out:0"),
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

        response = self.client.post(
            f"/dashboard/medicines/{medicine_id}/refill",
            data={"csrf_token": self.csrf_token(self.client), "strips": "1"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("10", response.get_data(as_text=True))

        connection = connect_database(self.database_path)
        try:
            remaining = connection.execute(
                """
                SELECT COUNT(*) FROM medicine_tablets
                WHERE medicine_id = ? AND consumed_at IS NULL
                """,
                (medicine_id,),
            ).fetchone()[0]
            self.assertEqual(remaining, 10)
        finally:
            connection.close()

    def test_inventory_limit_matches_application_limit(self):
        self.assertEqual(MAX_TABLETS_PER_MEDICINE, 1000)


if __name__ == "__main__":
    unittest.main()
