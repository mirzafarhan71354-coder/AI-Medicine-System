import argparse
import logging
import os
import time

from reminder_db import SCHEMA
from reminder_service import connect_worker_database, process_notification_cycle


logger = logging.getLogger("carepoint.reminder_worker")


def initialize_worker_database():
    connection = connect_worker_database()
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()


def run_worker(once=False, interval=None):
    initialize_worker_database()
    poll_seconds = interval or int(os.environ.get("REMINDER_POLL_SECONDS", "30"))
    if not 5 <= poll_seconds <= 3600:
        raise ValueError("REMINDER_POLL_SECONDS must be between 5 and 3600.")

    while True:
        try:
            processed = process_notification_cycle()
            if processed:
                logger.info("Processed %s reminder or stock alert event(s).", processed)
        except Exception:
            logger.exception("Reminder processing cycle failed.")
        if once:
            return
        time.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser(description="CarePoint scheduled reminder worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="process one cycle and exit (useful for checks and scheduled jobs)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        help="polling interval in seconds (default: REMINDER_POLL_SECONDS or 30)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_worker(once=args.once, interval=args.interval)


if __name__ == "__main__":
    main()
