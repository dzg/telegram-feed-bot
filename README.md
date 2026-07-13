## About

FeedBot is a Telegram bot that will help you organize many separate channels into a single beautiful feed.

> **To use this bot, you need to have two Telegram accounts (and two phone numbers, properly).**

## Installation

1. Install dependencies with requirements.txt:

```
pip install -r requirements.txt
```

or poetry:

```
poetry install
```

2. Set up Telephon ([documentation](https://docs.telethon.dev/en/stable/basic/signing-in.html)). When logging in with a terminal, use info of a secondary account - it will forward messages from channels to your main account.

3. Enter your data in config.ini (you have received *api_id* and *api_hash* after Telephon installation; you can find your main account *user_id* and your secondary account *telephon_user_id* with @userinfobot)

## How to use

You can add a channel to feed by forwarding a message from it to the secondary account (*telephon_user_id*). If channel is already in the database, forwarding a message will remove it from feed. To check up channels list, send '/channels'.

You will receive new posts from listed channels in the same dialogue.

## Control commands

The bot logs in as your own user account, so its **Saved Messages** (the chat with yourself) is the command surface — only you can post there, and it's synced across your devices. Send these from any device into Saved Messages:

- `/status` (or `/ping`) — confirm the bot is alive; shows uptime, version, target, and source channels
- `/channels` — list the configured source channels
- `/pull <time>` — backfill: fetch the last `<time>` of messages from **all** source feeds, clean/translate them, and post to the target channel. Time units: `m` (minutes), `h` (hours), `d` (days). Examples: `/pull 2h`, `/pull 90m`, `/pull 1d`.
- `/help` — list available commands

> `/pull` de-duplicates against what has already been forwarded during the current run, so overlapping windows won't double-post. Note that this de-dup memory resets when the service restarts.

## Running as a service (systemd)

The bot is designed to run unattended. Example unit (`/etc/systemd/system/telegram-feed-bot.service`):

```ini
[Unit]
Description=Telegram News Feed Aggregator Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/telegram-feed-bot
ExecStart=/path/to/telegram-feed-bot/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then: `sudo systemctl enable --now telegram-feed-bot`.
