# ActualNotify

Pulse-check your spending!

# Table of Contents
- [ActualNotify](#actualnotify)
- [Table of Contents](#table-of-contents)
- [Overview](#overview)
- [Technologies](#technologies)
- [How It Works](#how-it-works)
  - [Budget Monitoring Process](#budget-monitoring-process)
  - [Encrypted Snapshot System](#encrypted-snapshot-system)
  - [Notification Delivery](#notification-delivery)
- [Getting Started](#getting-started)
  - [Installation](#installation)
  - [Configuration](#configuration)
    - [Environment Variables](#environment-variables)
      - [Required Variables](#required-variables)
      - [Actual Budget Configuration](#actual-budget-configuration)
      - [Notification Configuration](#notification-configuration)
      - [Snapshot Configuration](#snapshot-configuration)
      - [Template Customization](#template-customization)
- [Usage](#usage)
  - [Running Manually](#running-manually)
  - [Scheduled Execution](#scheduled-execution)
    - [Using Cron (Linux/macOS)](#using-cron-linuxmacos)
    - [Using Windows Task Scheduler](#using-windows-task-scheduler)
    - [Using systemd Timer (Linux)](#using-systemd-timer-linux)
  - [Custom Notification Commands](#custom-notification-commands)
    - [ntfy.sh](#ntfysh)
    - [Telegram Bot](#telegram-bot)
    - [Discord Webhook](#discord-webhook)
    - [Email (using mail command)](#email-using-mail-command)
    - [Custom Script](#custom-script)

# Overview

ActualNotify is a lightweight budget monitoring tool that sends notifications when your spending approaches category budget limits in Actual Budget. It queries your Actual Budget server, calculates spending percentages for each budget category, and sends customizable notifications through external commands or logging when thresholds are exceeded.

The system includes an encrypted snapshot mechanism to prevent duplicate notifications and supports custom Jinja2 templates for notification formatting, making it ideal for integration with notification systems, chat platforms, or alerting services.

# Technologies
This project is created with:
- [ActualPy](https://actualpy.readthedocs.io/): 0.15-0.16
- [Jinja2](https://jinja.palletsprojects.com/): 3.1.6
- [Cryptography](https://cryptography.io/): (for encrypted snapshots)

# How It Works

## Budget Monitoring Process

ActualNotify connects to your Actual Budget server and performs the following steps:

1. **Fetch Budget Data**: Retrieves all budget categories and their allocated amounts for the current month
2. **Calculate Spending**: For each category, calculates the remaining balance using `get_accumulated_budgeted_balance()`
3. **Compute Usage**: Determines the percentage spent by comparing allocated vs. remaining amounts
4. **Threshold Check**: Compares usage percentage against the configured threshold (default: 70%)
5. **Notification Decision**: Sends notification only if threshold exceeded AND balance has changed since last run (unless `REPEAT_NOTIFY` is enabled)

## Encrypted Snapshot System

To prevent duplicate notifications when the script runs multiple times per day:

- **Snapshot Creation**: After each run, saves an encrypted snapshot containing `{category_id: remaining_balance}` pairs
- **Encryption**: Uses Fernet symmetric encryption with a key derived from your `ACTUAL_SERVER_PASSWORD` via PBKDF2-HMAC-SHA256 (390,000 iterations)
- **Change Detection**: On subsequent runs, compares current balances against the snapshot to detect changes
- **Fail-Safe Behavior**: If decryption fails (wrong password or corrupted file), starts with a blank snapshot rather than failing
- **Storage Format**: JSON file with base64-encoded salt and encrypted data token

## Notification Delivery

Notifications are delivered via customizable external commands:

1. **Template Rendering**: Uses Jinja2 to render the notification message with variables like `category`, `total`, `spent`, `remaining`, `used_fraction`, and `THRESHOLD`
2. **Command Execution**: Passes the rendered message to the configured command via stdin
3. **Output Streaming**: Streams stdout and stderr from the command into Python's logging system in real-time
4. **Error Handling**: Logs and raises exceptions if the command exits with a non-zero status
5. **Fallback**: If no command is configured, logs the rendered notification message

# Getting Started

## Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Pop101/ActualNotify.git
   cd ActualNotify
   ```

2. **Install dependencies** (using Poetry):
   ```bash
   poetry install
   ```

   Or install the `cryptography` package manually:
   ```bash
   pip install actualpy jinja2 cryptography
   ```

3. **Set up environment variables** (see [Configuration](#configuration))

## Configuration

### Environment Variables

#### Required Variables

These variables must be set for ActualNotify to connect to your Actual Budget server:

| Variable | Description | Default |
|----------|-------------|---------|
| `ACTUAL_SERVER_URL` | URL of your Actual Budget server | `http://localhost:5006` |
| `ACTUAL_SERVER_PASSWORD` | Actual Budget server password (also used for snapshot encryption) | `password` |
| `ACTUAL_SERVER_FILE` | Name of your budget file in Actual Budget | `My Finances` |

#### Actual Budget Configuration

If you're running Actual Budget locally or on a custom server:

```bash
export ACTUAL_SERVER_URL="http://localhost:5006"
export ACTUAL_SERVER_PASSWORD="your-actual-password"
export ACTUAL_SERVER_FILE="My Budget"
```

For Actual Budget hosted services, use the appropriate server URL.

#### Notification Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `BUDGET_WARNING_THRESHOLD` | Decimal threshold (0.0-1.0) for triggering notifications | `0.7` (70%) |
| `REPEAT_NOTIFY` | If `1` or `true`, always notify regardless of snapshot comparison | `0` (false) |
| `COMMAND_TO_RUN` | External command to execute for notifications (receives message via stdin) | _(none, logs instead)_ |

Example:
```bash
export BUDGET_WARNING_THRESHOLD="0.8"  # Notify at 80% spending
export REPEAT_NOTIFY="0"  # Only notify on changes
export COMMAND_TO_RUN="ntfy publish budget-alerts"  # Example using ntfy
```

#### Snapshot Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `BUDGET_SNAPSHOT_FILE` | Path to the encrypted snapshot file | `budget_snapshot.enc` |

Example:
```bash
export BUDGET_SNAPSHOT_FILE="/var/lib/actualnotify/snapshot.enc"
```

**Note**: The snapshot is encrypted using your `ACTUAL_SERVER_PASSWORD`.

#### Template Customization

| Variable | Description |
|----------|-------------|
| `STDIN_TEMPLATE` | Jinja2 template string for notification messages |

The template has access to these variables:
- `category` - The budget category object (with `.name`, `.id`, etc.)
- `total` - Total allocated budget amount (Decimal)
- `spent` - Amount spent so far (Decimal)
- `remaining` - Amount remaining (Decimal)
- `used_fraction` - Decimal percentage spent (0.0-1.0+)
- `THRESHOLD` - The configured threshold value
- `now` - Current date in ISO format

**Default Template**:
```jinja2
{% if used_fraction is defined and used_fraction >= THRESHOLD -%}
Budget Warning: 
{%- else -%}
Budget Notice: 
{%- endif -%}
You have spent ${{ spent | float | round(2) }} out of ${{ total | float | round(2) }} ({{ (used_fraction * 100) | round(1) }}%) of your {{ category.name | default('general') }} budget.
```

This renders to 
```
Budget Notice:You have spent 439.0 out of 500.0 (98.6%) of your Vehicle Maintenance budget.
```

# Usage

## Running Manually

Execute the script directly:

```bash
poetry run python script.py
```

Or if installed globally:

```bash
python script.py
```

The script will:
1. Connect to your Actual Budget server
2. Check all budget categories for the current month
3. Send notifications for categories exceeding the threshold
4. Save an encrypted snapshot for the next run

## Scheduled Execution

### Using Cron (Linux/macOS)

Add to your crontab (`crontab -e`):

```bash
# Run every 6 hours
0 */6 * * * cd /path/to/ActualNotify && /usr/bin/poetry run python script.py >> /var/log/actualnotify.log 2>&1

# Run daily at 9 AM
0 9 * * * cd /path/to/ActualNotify && /usr/bin/poetry run python script.py
```

### Using Windows Task Scheduler

1. Open Task Scheduler
2. Create a new task with trigger (e.g., daily at 9 AM)
3. Action: Start a program
   - Program: `poetry`
   - Arguments: `run python script.py`
   - Start in: `C:\path\to\ActualNotify`

### Using systemd Timer (Linux)

Create `/etc/systemd/system/actualnotify.service`:

```ini
[Unit]
Description=ActualNotify Budget Monitor
After=network.target

[Service]
Type=oneshot
User=youruser
WorkingDirectory=/path/to/ActualNotify
Environment="ACTUAL_SERVER_URL=http://localhost:5006"
Environment="ACTUAL_SERVER_PASSWORD=yourpassword"
Environment="ACTUAL_SERVER_FILE=My Budget"
ExecStart=/usr/bin/poetry run python script.py
```

Create `/etc/systemd/system/actualnotify.timer`:

```ini
[Unit]
Description=Run ActualNotify every 6 hours

[Timer]
OnCalendar=*-*-* 00,06,12,18:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now actualnotify.timer
```

## Custom Notification Commands

ActualNotify can integrate with various notification systems by passing the rendered message via stdin:

### ntfy.sh
```bash
export COMMAND_TO_RUN="curl -d @- ntfy.sh/budget-alerts"
```

### Telegram Bot
```bash
export COMMAND_TO_RUN="telegram-send --stdin"
```

### Discord Webhook
```bash
export COMMAND_TO_RUN="discord-webhook --stdin"
```

### Email (using mail command)
```bash
export COMMAND_TO_RUN="mail -s 'Budget Alert' your@email.com"
```

### Custom Script
```bash
export COMMAND_TO_RUN="/usr/local/bin/my-notifier.sh"
```

The command will receive the formatted notification message on stdin and should exit with code 0 on success.
