import os
import logging
import calendar
from datetime import date
from decimal import Decimal
import json
import base64
import subprocess
import threading

from jinja2 import Template

# Configure logging to console and file (append mode)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('transaction_notifications.log', mode='a'),
        logging.StreamHandler()
    ]
)

# Load configuration from environment variables
ACTUAL_SERVER_URL = os.getenv("ACTUAL_SERVER_URL", "http://localhost:5006")
ACTUAL_SERVER_PASSWORD = os.getenv("ACTUAL_SERVER_PASSWORD", "password")
ACTUAL_SERVER_FILE = os.getenv("ACTUAL_SERVER_FILE", "My Finances")

# Notification threshold and repeat control
THRESHOLD = Decimal(os.getenv("BUDGET_WARNING_THRESHOLD", "0.7"))
REPEAT_NOTIFY = os.getenv("REPEAT_NOTIFY", "0").lower() in ("1", "true")

# Linear spend trajectory extrapolation
ENABLE_SPEND_TRAJECTORY = os.getenv("ENABLE_SPEND_TRAJECTORY", "0").lower() in ("1", "true")
TRAJECTORY_THRESHOLD = Decimal(os.getenv("TRAJECTORY_THRESHOLD", "1.0"))

# Snapshot file and repeat control
SNAPSHOT_FILE = os.getenv("BUDGET_SNAPSHOT_FILE", "budget_snapshot.enc")

# Notification command and template for stdin
COMMAND_TO_RUN = os.getenv("COMMAND_TO_RUN", "")
STDIN_TEMPLATE = Template(os.getenv("STDIN_TEMPLATE", r"""
{%- if trajectory_triggered -%}
Budget Trajectory Warning:
{%- elif used_fraction is defined and used_fraction >= THRESHOLD -%}
Budget Warning:
{%- else -%}
Budget Notice:
{%- endif -%}
You have spent ${{ spent | float | round(2) }} out of ${{ total | float | round(2) }} ({{ (used_fraction * 100) | round(1) }}%) of your {{ category.name | default('general') }} budget.
{%- if trajectory_triggered %} At current pace (day {{ current_day }} of {{ days_in_month }}), projected to spend ${{ projected_spend | float | round(2) }} ({{ (projected_fraction * 100) | round(1) }}%) by month end.{% endif %}
""".strip()))

# Function to run command with stdin and stream output to logging
def run_command_with_stdin(command: str, stdin_text: str):
    logging.info("Running notifier command: %s", command)
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1,
        shell=True,
    )

    def _stream_reader(stream, level=logging.INFO):
        try:
            for line in iter(stream.readline, ""):
                if line:
                    logging.log(level, line.rstrip())
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_stream_reader, args=(proc.stdout, logging.INFO), daemon=True)
    t_err = threading.Thread(target=_stream_reader, args=(proc.stderr, logging.ERROR), daemon=True)
    t_out.start()
    t_err.start()

    # send stdin and close
    if stdin_text is not None:
        try:
            proc.stdin.write(stdin_text)
        except Exception:
            pass
    try:
        proc.stdin.close()
    except Exception:
        pass

    returncode = proc.wait()
    
    # ensure threads finished reading
    t_out.join(timeout=0.1)
    t_err.join(timeout=0.1)

    if returncode != 0:
        logging.error("Notifier command exited with code %s", returncode)

# Encrypted snapshot to handle double-notify and restarts
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.fernet import Fernet

def _derive_fernet_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
        backend=default_backend(),
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def save_snapshot(pairs: dict):
    """Encrypt and save snapshot dict {category_id: remaining_str}."""
    data = json.dumps(pairs).encode()
    salt = os.urandom(16)
    key = _derive_fernet_key(ACTUAL_SERVER_PASSWORD, salt)
    token = Fernet(key).encrypt(data)
    payload = {"salt": base64.b64encode(salt).decode(), "data": base64.b64encode(token).decode()}
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def load_snapshot() -> dict:
    """Load and decrypt snapshot. On wrong password or corruption return {}."""
    if not os.path.exists(SNAPSHOT_FILE):
        return {}
    try:
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return {}

    if "salt" in payload and "data" in payload:
        try:
            salt = base64.b64decode(payload["salt"])
            token = base64.b64decode(payload["data"])
            key = _derive_fernet_key(ACTUAL_SERVER_PASSWORD, salt)
            data = Fernet(key).decrypt(token)
            return json.loads(data.decode())
        except Exception:
            # invalid password or corrupted file -> start blank
            return {}
    return {}

# Go!
from actual import Actual
from actual.queries import get_categories, get_budgets, get_accumulated_budgeted_balance

with Actual(base_url=ACTUAL_SERVER_URL, password=ACTUAL_SERVER_PASSWORD, file=ACTUAL_SERVER_FILE) as actual:
    categories = get_categories(actual.session)
    category_by_id = {c.id: c for c in categories}
    budgets = get_budgets(actual.session, month=date.today())

    prev_snapshot = load_snapshot()
    current_snapshot = {}

    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    current_day = today.day

    for budget in budgets:
        if budget.category_id is None:
            continue
        category = category_by_id.get(budget.category_id)
        if category is None or category.is_income:
            continue

        total = budget.get_amount()
        if total <= Decimal(0):
            # record but skip
            current_snapshot[category.id] = str(get_accumulated_budgeted_balance(actual.session, date.today(), category))
            continue

        remaining = get_accumulated_budgeted_balance(actual.session, date.today(), category)
        current_snapshot[category.id] = str(remaining)

        spent = total - remaining
        used_fraction = spent / total

        # Linear trajectory: extrapolate current spend over remaining days
        if current_day > 0 and spent > Decimal(0):
            projected_spend = spent * Decimal(days_in_month) / Decimal(current_day)
        else:
            projected_spend = spent
        projected_fraction = projected_spend / total if total > Decimal(0) else Decimal(0)

        threshold_triggered = used_fraction >= THRESHOLD
        trajectory_triggered = (
            ENABLE_SPEND_TRAJECTORY
            and not threshold_triggered
            and projected_fraction >= TRAJECTORY_THRESHOLD
        )
        any_trigger = threshold_triggered or trajectory_triggered

        should_notify = REPEAT_NOTIFY or (prev_snapshot.get(category.id) != str(remaining))
        logging.info(
            "Budget check: category '%s' spent %s of %s (%.1f%% used, %.1f%% projected by EOM), notify: %s",
            category.name,
            spent,
            total,
            float(used_fraction * 100),
            float(projected_fraction * 100),
            should_notify,
        )
        if any_trigger and should_notify:
            pct = float(used_fraction * 100)
            now = date.today().isoformat()
            rendered = STDIN_TEMPLATE.render(**locals())

            if COMMAND_TO_RUN:
                try:
                    run_command_with_stdin(COMMAND_TO_RUN, rendered)
                except subprocess.CalledProcessError as e:
                    logging.exception("Notifier command failed: %s", e)
                    # Re-raise so calling environment can react if desired
                    raise
            else:
                # Fallback: no command specified; log the rendered message
                logging.info("No COMMAND_TO_RUN set; would notify with:\n%s", rendered)

    # Save snapshot for next run
    try:
        save_snapshot(current_snapshot)
    except Exception as e:
        logging.exception("Failed to save budget snapshot: %s", e)
