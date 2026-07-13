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

Send these as a private message to the bot account. They are accepted **only** from the owner account (`user_id` in `config.ini`):

- `/status` (or `/ping`) — confirm the bot is alive; shows uptime, git version, target, and source channels
- `/channels` — list the configured source channels
- `/update` — fast-forward `git pull` and restart to apply the new code
- `/help` — list available commands

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

Then: `sudo systemctl enable --now telegram-feed-bot`. The `/update` command requires the service user to be able to run `sudo systemctl restart telegram-feed-bot.service` without a password.
