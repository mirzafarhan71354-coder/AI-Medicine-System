from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr
import hmac
import json
import re
import secrets
from urllib.parse import urlparse

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
import sqlite3

from reminder_db import get_database
from reminder_service import (
    MAX_MEDICINES_PER_SUBMISSION,
    MAX_TABLETS_PER_MEDICINE,
    MAX_SNOOZE_MINUTES,
    apply_dose_action,
    get_dose_schedule,
    get_medicine_inventory,
    validate_timezone,
)


bp = Blueprint("accounts", __name__)
MOBILE_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_PUSH_SUFFIXES = (
    ".push.services.mozilla.com",
    ".fcm.googleapis.com",
    ".push.apple.com",
    ".notify.windows.com",
)
WEEKDAYS = {
    "0": "Monday",
    "1": "Tuesday",
    "2": "Wednesday",
    "3": "Thursday",
    "4": "Friday",
    "5": "Saturday",
    "6": "Sunday",
}


def _csrf_token():
    token = session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@bp.before_app_request
def load_current_user():
    user_id = session.get("user_id")
    g.current_user = None
    if user_id is not None:
        g.current_user = get_database().execute(
            "SELECT id, name, email, mobile, timezone, sms_enabled FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if g.current_user is None:
            session.clear()


@bp.before_request
def protect_account_forms():
    if request.method != "POST":
        return None
    token = request.form.get("csrf_token")
    if not token:
        payload = request.get_json(silent=True) or {}
        token = payload.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not token or not hmac.compare_digest(token, _csrf_token()):
        abort(400, description="Your form expired. Refresh the page and try again.")
    return None


@bp.after_app_request
def prevent_account_page_caching(response):
    if request.blueprint == bp.name:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Pragma"] = "no-cache"
    return response


@bp.app_context_processor
def inject_account_context():
    return {
        "current_user": getattr(g, "current_user", None),
        "csrf_token": _csrf_token,
    }


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if getattr(g, "current_user", None) is None:
            flash("Sign in to open your CarePoint account.", "info")
            if request.method == "GET":
                return redirect(url_for("accounts.login", next=request.path))
            return redirect(url_for("accounts.login"))
        return view(*args, **kwargs)

    return wrapped


def _valid_email(email):
    parsed = parseaddr(email)
    return (
        len(email) <= 254
        and parsed[1] == email
        and EMAIL_PATTERN.fullmatch(email) is not None
    )


def _render_auth(mode, error=None, values=None, status=200):
    return render_template(
        "auth.html",
        mode=mode,
        error=error,
        values=values or {},
    ), status


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.current_user is not None:
        return redirect(url_for("tools"))
    if request.method == "GET":
        return _render_auth("register")

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    mobile = request.form.get("mobile", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", "")
    values = {"name": name, "email": email, "mobile": mobile}
    error = None
    if not 2 <= len(name) <= 100:
        error = "Enter a name between 2 and 100 characters."
    elif not _valid_email(email):
        error = "Enter a valid email address."
    elif not MOBILE_PATTERN.fullmatch(mobile):
        error = "Use an international phone number, for example +14155552671."
    elif len(password) < 12 or len(password) > 128:
        error = "Choose a password between 12 and 128 characters."
    elif password != confirm:
        error = "The passwords do not match."
    if error:
        return _render_auth("register", error, values, 400)

    connection = get_database()
    try:
        cursor = connection.execute(
            """
            INSERT INTO users (name, email, mobile, password_hash)
            VALUES (?, ?, ?, ?)
            """,
            (
                name,
                email,
                mobile,
                generate_password_hash(password, method="scrypt"),
            ),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        return _render_auth(
            "register",
            "An account already uses that email address or mobile number.",
            values,
            409,
        )

    session.clear()
    session["user_id"] = cursor.lastrowid
    session.permanent = True
    _csrf_token()
    flash("Your account is ready. Add a medicine to set up reminders.", "success")
    return redirect(url_for("tools"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.current_user is not None:
        return redirect(url_for("tools"))
    if request.method == "GET":
        return _render_auth("login")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    user = get_database().execute(
        "SELECT id, password_hash FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    if (
        user is None or len(email) > 254 or len(password) > 128
        or not check_password_hash(user["password_hash"], password)
    ):
        return _render_auth(
            "login",
            "Email or password is incorrect.",
            {"email": email},
            401,
        )

    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    _csrf_token()
    destination = request.args.get("next")
    if destination:
        parsed_destination = urlparse(destination)
        if (
            destination.startswith("/")
            and not destination.startswith("//")
            and "\\" not in destination
            and not parsed_destination.scheme
            and not parsed_destination.netloc
        ):
            return redirect(destination)
    return redirect(url_for("tools"))


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have signed out.", "success")
    return redirect(url_for("home"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        mobile = request.form.get("mobile", "").strip()
        timezone_name = request.form.get("timezone", "").strip()
        sms_enabled = request.form.get("sms_enabled") == "on"
        current_password = request.form.get("current_password", "")
        password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        credential_change = (
            email != g.current_user["email"]
            or mobile != g.current_user["mobile"]
            or bool(password)
        )
        if not 2 <= len(name) <= 100:
            error = "Enter a name between 2 and 100 characters."
        elif not _valid_email(email):
            error = "Enter a valid email address."
        elif not MOBILE_PATTERN.fullmatch(mobile):
            error = "Use an international phone number such as +14155552671."
        elif validate_timezone(timezone_name) is None:
            error = "Enter a valid IANA timezone, such as Asia/Kolkata."
        elif password and len(password) < 12:
            error = "A new password must contain at least 12 characters."
        elif len(password) > 128:
            error = "The new password is too long."
        elif password != confirm:
            error = "The new passwords do not match."
        elif len(current_password) > 128:
            error = "The current password is too long."
        elif credential_change:
            saved = get_database().execute(
                "SELECT password_hash FROM users WHERE id = ?",
                (g.current_user["id"],),
            ).fetchone()
            if not current_password or not check_password_hash(
                saved["password_hash"], current_password
            ):
                error = "Enter your current password to change your email, mobile, or password."
        if error is None:
            connection = get_database()
            try:
                connection.execute("BEGIN IMMEDIATE")
                if timezone_name != g.current_user["timezone"]:
                    connection.execute(
                        """
                        DELETE FROM dose_events
                        WHERE user_id = ? AND status IN ('pending', 'snoozed')
                        """,
                        (g.current_user["id"],),
                    )
                if password:
                    connection.execute(
                        """
                        UPDATE users
                        SET name = ?, email = ?, mobile = ?, timezone = ?,
                            sms_enabled = ?, password_hash = ?
                        WHERE id = ?
                        """,
                        (
                            name, email, mobile, timezone_name, int(sms_enabled),
                            generate_password_hash(password, method="scrypt"),
                            g.current_user["id"],
                        ),
                    )
                    session.clear()
                    flash("Profile and password updated. Please sign in again.", "success")
                    connection.commit()
                    return redirect(url_for("accounts.login"))
                connection.execute(
                    """
                    UPDATE users
                    SET name = ?, email = ?, mobile = ?, timezone = ?, sms_enabled = ?
                    WHERE id = ?
                    """,
                    (
                        name, email, mobile, timezone_name, int(sms_enabled),
                        g.current_user["id"],
                    ),
                )
                connection.commit()
                flash("Profile updated.", "success")
                return redirect(url_for("accounts.profile"))
            except sqlite3.IntegrityError:
                connection.rollback()
                error = "An account already uses that email address or mobile number."

    push_configured = all(
        current_app.config.get(key) for key in (
            "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"
        )
    )
    sms_configured = all(
        current_app.config.get(key) for key in (
            "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"
        )
    )
    last_sms_delivery = get_database().execute(
        """
        SELECT n.event_type, n.message, d.status, d.error_message,
               d.provider_reference, d.created_at
        FROM notification_deliveries AS d
        JOIN notification_history AS n ON n.id = d.notification_id
        WHERE n.user_id = ? AND d.channel = 'sms'
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT 1
        """,
        (g.current_user["id"],),
    ).fetchone()
    return render_template(
        "profile.html",
        error=error,
        user=g.current_user,
        sms_configured=sms_configured,
        last_sms_delivery=last_sms_delivery,
        push_configured=push_configured,
        vapid_public_key=current_app.config.get("VAPID_PUBLIC_KEY", ""),
    )


@bp.route("/dashboard")
@login_required
def dashboard():
    connection = get_database()
    user = g.current_user
    doses = get_dose_schedule(connection, user["id"], user["timezone"])
    medicines = get_medicine_inventory(connection, user["id"], user["timezone"])
    notifications = connection.execute(
        """
        SELECT n.id, n.event_type, n.message, n.created_at,
               GROUP_CONCAT(d.channel || ':' || d.status, ',') AS delivery_status
        FROM notification_history AS n
        LEFT JOIN notification_deliveries AS d ON d.notification_id = n.id
        WHERE n.user_id = ?
        GROUP BY n.id
        ORDER BY n.created_at DESC
        LIMIT 30
        """,
        (user["id"],),
    ).fetchall()
    zone = validate_timezone(user["timezone"])
    if zone is None:
        flash("Your saved timezone is invalid. Update your profile to resume reminders.", "error")
        return redirect(url_for("accounts.profile"))
    today_date = datetime.now(zone).date()
    today = today_date.isoformat()
    default_end_date = (today_date + timedelta(days=30)).isoformat()
    dose_history = connection.execute(
        """
        SELECT d.scheduled_for, d.status, d.action_at,
               m.name AS medicine_name, m.instructions
        FROM dose_events AS d
        JOIN medicines AS m ON m.id = d.medicine_id AND m.user_id = d.user_id
        WHERE d.user_id = ? AND d.status IN ('taken', 'skipped', 'snoozed')
          AND substr(d.scheduled_for, 1, 10) >= ?
        ORDER BY d.scheduled_for DESC
        LIMIT 50
        """,
        (user["id"], (today_date - timedelta(days=30)).isoformat()),
    ).fetchall()
    return render_template(
        "dashboard.html",
        user=user,
        medicines=medicines,
        doses=doses,
        today=today,
        default_end_date=default_end_date,
        dose_history=dose_history,
        notifications=notifications,
        vapid_public_key=current_app.config.get("VAPID_PUBLIC_KEY", ""),
        csrf= _csrf_token(),
        error=request.args.get("error"),
    )


@bp.route("/dashboard/medicines/add", methods=["POST"])
@login_required
def add_medicines():
    names = [
        line.strip()
        for line in request.form.get("medicine_names", "").splitlines()
        if line.strip()
    ]
    instructions = request.form.get("instructions", "").strip()
    start_text = request.form.get("start_date", "").strip()
    end_text = request.form.get("end_date", "").strip()
    times = list(dict.fromkeys(
        value.strip() for value in request.form.getlist("reminder_times")
        if value.strip()
    ))
    days = set(request.form.getlist("days_of_week"))
    try:
        tablets_per_strip = int(request.form.get("tablets_per_strip", ""))
        strips = int(request.form.get("strips", ""))
        tablets_per_dose = int(request.form.get("tablets_per_dose", ""))
        threshold = int(request.form.get("low_stock_threshold", ""))
        start_date = date.fromisoformat(start_text)
        end_date = date.fromisoformat(end_text)
        for reminder_time in times:
            datetime.strptime(reminder_time, "%H:%M")
    except (ValueError, TypeError):
        flash("Check the dates, strip quantities, dosage, threshold, and reminder times.", "error")
        return redirect(url_for("accounts.dashboard"))

    if not times:
        flash("Choose at least one reminder time before saving the medicine.", "error")
        return redirect(url_for("accounts.dashboard"))

    if (
        not names or len(names) > MAX_MEDICINES_PER_SUBMISSION
        or any(len(name) > 120 for name in names)
        or not instructions or len(instructions) > 500
        or len(times) > 8
        or not days or any(day not in WEEKDAYS for day in days)
        or not 1 <= tablets_per_strip <= 100
        or not 1 <= strips <= MAX_TABLETS_PER_MEDICINE
        or tablets_per_strip * strips > MAX_TABLETS_PER_MEDICINE
        or not 1 <= tablets_per_dose <= 100
        or tablets_per_dose > tablets_per_strip * strips
        or not 0 <= threshold <= MAX_TABLETS_PER_MEDICINE
        or end_date < start_date or end_date < date.today()
    ):
        flash("Review the medicine names, schedule, dates, and inventory values.", "error")
        return redirect(url_for("accounts.dashboard"))
    days = sorted(days, key=int)

    connection = get_database()
    try:
        connection.execute("BEGIN IMMEDIATE")
        for name in names:
            cursor = connection.execute(
                """
                INSERT INTO medicines
                    (user_id, name, instructions, start_date, end_date,
                     tablets_per_strip, tablets_per_dose, low_stock_threshold)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    g.current_user["id"], name, instructions, start_text,
                    end_text, tablets_per_strip, tablets_per_dose, threshold,
                ),
            )
            medicine_id = cursor.lastrowid
            connection.executemany(
                """
                INSERT INTO medicine_tablets (medicine_id, tablet_number)
                VALUES (?, ?)
                """,
                ((medicine_id, index) for index in range(1, tablets_per_strip * strips + 1)),
            )
            connection.executemany(
                """
                INSERT INTO medicine_schedules
                    (user_id, medicine_id, time_local, days_of_week)
                VALUES (?, ?, ?, ?)
                """,
                (
                    (
                        g.current_user["id"], medicine_id, reminder_time,
                        ",".join(days),
                    )
                    for reminder_time in times
                ),
            )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        flash("Could not save the medicine schedule. Please review the entries.", "error")
        return redirect(url_for("accounts.dashboard"))
    flash(f"Added {len(names)} medicine(s) and their reminders.", "success")
    return redirect(url_for("accounts.dashboard"))


@bp.route("/dashboard/medicines/<int:medicine_id>/edit", methods=["POST"])
@login_required
def edit_medicine(medicine_id):
    name = request.form.get("name", "").strip()
    instructions = request.form.get("instructions", "").strip()
    start_text = request.form.get("start_date", "").strip()
    end_text = request.form.get("end_date", "").strip()
    try:
        start_date = date.fromisoformat(start_text)
        end_date = date.fromisoformat(end_text)
    except ValueError:
        flash("Enter valid start and end dates.", "error")
        return redirect(url_for("accounts.dashboard"))
    if (
        not 1 <= len(name) <= 120 or not 1 <= len(instructions) <= 500
        or end_date < start_date
    ):
        flash("Enter a medicine name, dosage instructions, and valid dates.", "error")
        return redirect(url_for("accounts.dashboard"))
    connection = get_database()
    try:
        threshold = int(request.form.get("low_stock_threshold", ""))
        tablets_per_dose = int(request.form.get("tablets_per_dose", ""))
    except ValueError:
        flash("Enter valid dosage and low-stock quantities.", "error")
        return redirect(url_for("accounts.dashboard"))
    inventory = connection.execute(
        """
        SELECT m.tablets_per_strip, m.start_date, m.end_date,
               COUNT(t.id) AS total_capacity
        FROM medicines AS m
        LEFT JOIN medicine_tablets AS t ON t.medicine_id = m.id
        WHERE m.id = ? AND m.user_id = ?
        GROUP BY m.id
        """,
        (medicine_id, g.current_user["id"]),
    ).fetchone()
    if (
        inventory is None
        or not 1 <= tablets_per_dose <= min(
            100, inventory["total_capacity"]
        )
        or not 0 <= threshold <= MAX_TABLETS_PER_MEDICINE
    ):
        flash("Dosage must fit the medicine inventory and the stock threshold must be valid.", "error")
        return redirect(url_for("accounts.dashboard"))
    if (
        start_text != inventory["start_date"]
        or end_text != inventory["end_date"]
    ):
        connection.execute(
            """
            DELETE FROM dose_events
            WHERE medicine_id = ? AND user_id = ?
              AND status IN ('pending', 'snoozed')
            """,
            (medicine_id, g.current_user["id"]),
        )
    cursor = connection.execute(
        """
        UPDATE medicines
        SET name = ?, instructions = ?, start_date = ?, end_date = ?,
            tablets_per_dose = ?, low_stock_threshold = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            name, instructions, start_text, end_text, tablets_per_dose, threshold,
            medicine_id, g.current_user["id"],
        ),
    )
    connection.commit()
    flash("Medicine details updated." if cursor.rowcount else "Medicine not found.", "success")
    return redirect(url_for("accounts.dashboard"))


@bp.route("/dashboard/medicines/<int:medicine_id>/refill", methods=["POST"])
@login_required
def refill_medicine(medicine_id):
    try:
        strips = int(request.form.get("strips", ""))
    except ValueError:
        flash("Enter a whole number of strips to add.", "error")
        return redirect(url_for("accounts.dashboard"))
    connection = get_database()
    connection.execute("BEGIN IMMEDIATE")
    medicine = connection.execute(
        """
        SELECT m.tablets_per_strip, COUNT(t.id) AS capacity
        FROM medicines AS m
        LEFT JOIN medicine_tablets AS t ON t.medicine_id = m.id
        WHERE m.id = ? AND m.user_id = ?
        GROUP BY m.id
        """,
        (medicine_id, g.current_user["id"]),
    ).fetchone()
    if medicine is None:
        connection.rollback()
        flash("Medicine not found.", "error")
        return redirect(url_for("accounts.dashboard"))
    add_count = strips * medicine["tablets_per_strip"]
    if strips < 1 or add_count > MAX_TABLETS_PER_MEDICINE - medicine["capacity"]:
        connection.rollback()
        flash(
            f"Refill must add at least one strip and keep the inventory under "
            f"{MAX_TABLETS_PER_MEDICINE:,} tablets.",
            "error",
        )
        return redirect(url_for("accounts.dashboard"))
    start = medicine["capacity"] + 1
    connection.executemany(
        "INSERT INTO medicine_tablets (medicine_id, tablet_number) VALUES (?, ?)",
        ((medicine_id, index) for index in range(start, start + add_count)),
    )
    connection.execute(
        "UPDATE medicines SET alert_state = 'ok' WHERE id = ? AND user_id = ?",
        (medicine_id, g.current_user["id"]),
    )
    connection.commit()
    flash(f"Added {strips} strip(s) to inventory.", "success")
    return redirect(url_for("accounts.dashboard"))


@bp.route("/dashboard/medicines/<int:medicine_id>/delete", methods=["POST"])
@login_required
def delete_medicine(medicine_id):
    connection = get_database()
    cursor = connection.execute(
        "DELETE FROM medicines WHERE id = ? AND user_id = ?",
        (medicine_id, g.current_user["id"]),
    )
    connection.commit()
    flash("Medicine and its reminder history deleted." if cursor.rowcount else "Medicine not found.", "success")
    return redirect(url_for("accounts.dashboard"))


@bp.route("/dashboard/tablets/<int:tablet_id>", methods=["POST"])
@login_required
def set_tablet_status(tablet_id):
    desired = request.form.get("state")
    if desired not in {"consumed", "available"}:
        abort(400, description="Choose consumed or available.")
    connection = get_database()
    connection.execute("BEGIN IMMEDIATE")
    tablet = connection.execute(
        """
        SELECT t.id, t.medicine_id, t.consumed_at
        FROM medicine_tablets AS t
        JOIN medicines AS m ON m.id = t.medicine_id
        WHERE t.id = ? AND m.user_id = ?
        """,
        (tablet_id, g.current_user["id"]),
    ).fetchone()
    if tablet is None:
        connection.rollback()
        abort(404)
    is_consumed = tablet["consumed_at"] is not None
    target_consumed = desired == "consumed"
    if is_consumed == target_consumed:
        connection.commit()
        return redirect(url_for("accounts.dashboard"))
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if target_consumed:
        connection.execute(
            """
            UPDATE medicine_tablets
            SET consumed_at = ?, consumption_source = 'manual', dose_id = NULL
            WHERE id = ? AND consumed_at IS NULL
            """,
            (timestamp, tablet_id),
        )
        event_type, quantity = "manual_consumption", -1
    else:
        connection.execute(
            """
            UPDATE medicine_tablets
            SET consumed_at = NULL, consumption_source = NULL, dose_id = NULL
            WHERE id = ?
            """,
            (tablet_id,),
        )
        event_type, quantity = "manual_correction", 1
    connection.execute(
        """
        INSERT INTO inventory_events
            (user_id, medicine_id, tablet_id, event_type, quantity)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            g.current_user["id"], tablet["medicine_id"], tablet_id,
            event_type, quantity,
        ),
    )
    connection.commit()
    return redirect(url_for("accounts.dashboard"))


@bp.route("/dashboard/schedules/<int:schedule_id>/edit", methods=["POST"])
@login_required
def edit_schedule(schedule_id):
    reminder_time = request.form.get("time_local", "").strip()
    days = set(request.form.getlist("days_of_week"))
    try:
        datetime.strptime(reminder_time, "%H:%M")
    except ValueError:
        flash("Choose a valid reminder time.", "error")
        return redirect(url_for("accounts.dashboard"))
    if not days or any(day not in WEEKDAYS for day in days):
        flash("Choose at least one day for this reminder.", "error")
        return redirect(url_for("accounts.dashboard"))
    days = sorted(days, key=int)
    connection = get_database()
    try:
        connection.execute("BEGIN IMMEDIATE")
        local_date = datetime.now(
            validate_timezone(g.current_user["timezone"])
        ).date().isoformat()
        connection.execute(
            """
            DELETE FROM dose_events
            WHERE schedule_id = ? AND user_id = ?
              AND status IN ('pending', 'snoozed')
              AND substr(scheduled_for, 1, 10) >= ?
            """,
            (schedule_id, g.current_user["id"], local_date),
        )
        cursor = connection.execute(
            """
            UPDATE medicine_schedules
            SET time_local = ?, days_of_week = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                reminder_time, ",".join(days), schedule_id,
                g.current_user["id"],
            ),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        flash("That medicine already has a reminder at this time.", "error")
        return redirect(url_for("accounts.dashboard"))
    flash("Reminder updated." if cursor.rowcount else "Reminder not found.", "success")
    return redirect(url_for("accounts.dashboard"))


@bp.route("/dashboard/medicines/<int:medicine_id>/schedules/add", methods=["POST"])
@login_required
def add_schedule(medicine_id):
    reminder_time = request.form.get("time_local", "").strip()
    days = set(request.form.getlist("days_of_week"))
    try:
        datetime.strptime(reminder_time, "%H:%M")
    except ValueError:
        flash("Choose a valid reminder time.", "error")
        return redirect(url_for("accounts.dashboard"))
    if not days or any(day not in WEEKDAYS for day in days):
        flash("Choose at least one day for this reminder.", "error")
        return redirect(url_for("accounts.dashboard"))
    days = sorted(days, key=int)
    connection = get_database()
    try:
        cursor = connection.execute(
            """
            INSERT INTO medicine_schedules
                (user_id, medicine_id, time_local, days_of_week)
            SELECT ?, id, ?, ? FROM medicines
            WHERE id = ? AND user_id = ?
            """,
            (
                g.current_user["id"], reminder_time, ",".join(days),
                medicine_id, g.current_user["id"],
            ),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        flash("That medicine already has a reminder at this time.", "error")
        return redirect(url_for("accounts.dashboard"))
    flash("Reminder added." if cursor.rowcount else "Medicine not found.", "success")
    return redirect(url_for("accounts.dashboard"))


@bp.route("/dashboard/schedules/<int:schedule_id>/toggle", methods=["POST"])
@login_required
def toggle_schedule(schedule_id):
    enabled = request.form.get("enabled") == "1"
    connection = get_database()
    cursor = connection.execute(
        """
        UPDATE medicine_schedules SET enabled = ?
        WHERE id = ? AND user_id = ?
        """,
        (int(enabled), schedule_id, g.current_user["id"]),
    )
    connection.commit()
    flash("Reminder updated." if cursor.rowcount else "Reminder not found.", "success")
    return redirect(url_for("accounts.dashboard"))


@bp.route("/dashboard/schedules/<int:schedule_id>/delete", methods=["POST"])
@login_required
def delete_schedule(schedule_id):
    connection = get_database()
    cursor = connection.execute(
        "DELETE FROM medicine_schedules WHERE id = ? AND user_id = ?",
        (schedule_id, g.current_user["id"]),
    )
    connection.commit()
    flash("Reminder deleted." if cursor.rowcount else "Reminder not found.", "success")
    return redirect(url_for("accounts.dashboard"))


@bp.route("/dashboard/doses/<int:dose_id>/action", methods=["POST"])
@login_required
def dose_action(dose_id):
    action = request.form.get("action")
    try:
        snooze_minutes = int(request.form.get("snooze_minutes", "10"))
    except ValueError:
        snooze_minutes = 0
    succeeded, message = apply_dose_action(
        get_database(),
        g.current_user["id"],
        dose_id,
        action,
        snooze_minutes,
    )
    flash(message, "success" if succeeded else "error")
    return redirect(url_for("accounts.dashboard"))


@bp.route("/api/notifications")
@login_required
def notification_feed():
    rows = get_database().execute(
        """
        SELECT id, event_type, message, created_at
        FROM notification_history
        WHERE user_id = ? AND created_at >= datetime('now', '-1 day')
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (g.current_user["id"],),
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@bp.route("/api/push-subscriptions", methods=["POST"])
@login_required
def save_push_subscription():
    payload = request.get_json(silent=True) or {}
    subscription = payload.get("subscription")
    if not isinstance(subscription, dict):
        return jsonify({"error": "A browser push subscription is required."}), 400
    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys")
    if (
        not isinstance(endpoint, str) or len(endpoint) > 2048
        or not isinstance(keys, dict)
        or not all(isinstance(keys.get(key), str) for key in ("auth", "p256dh"))
        or any(len(keys[key]) > 256 for key in ("auth", "p256dh"))
        or len(json.dumps(subscription)) > 4096
    ):
        return jsonify({"error": "The push subscription is incomplete."}), 400
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").lower()
    allowed_host = any(
        host == suffix[1:] or host.endswith(suffix)
        for suffix in ALLOWED_PUSH_SUFFIXES
    )
    if parsed.scheme != "https" or not allowed_host:
        return jsonify({"error": "This browser push service is not supported."}), 400
    connection = get_database()
    existing = connection.execute(
        "SELECT user_id FROM push_subscriptions WHERE endpoint = ?",
        (endpoint,),
    ).fetchone()
    if existing is not None and existing["user_id"] != g.current_user["id"]:
        return jsonify({"error": "This push endpoint is already registered."}), 409
    connection.execute(
        """
        INSERT INTO push_subscriptions (user_id, endpoint, subscription_json)
        VALUES (?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET subscription_json = excluded.subscription_json
        """,
        (
            g.current_user["id"],
            endpoint,
            json.dumps(subscription, separators=(",", ":")),
        ),
    )
    connection.commit()
    return jsonify({"ok": True, "message": "Browser push reminders are enabled."})


@bp.route("/api/push-subscriptions", methods=["DELETE"])
@login_required
def delete_push_subscription():
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        return jsonify({"error": "A valid browser push endpoint is required."}), 400
    connection = get_database()
    connection.execute(
        "DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id = ?",
        (endpoint, g.current_user["id"]),
    )
    connection.commit()
    return jsonify({"ok": True})
