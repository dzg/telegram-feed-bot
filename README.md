## About

FeedBot is a Telegram bot that will help you organize many separate channels into a single beautiful feed.

> **To use this bot, you need to have two Telegram accounts (and two phone numbers, properly).**

## Installation

1. Install dependencies with requirements.txt:

```
pip install -r requirements.txt
```

> **Telegram schema layer:** `requirements.txt` intentionally installs a
> **vendored Telethon built against schema layer 228** (`vendor/`), not the PyPI
> release. Telegram's servers are on layer 228; the newest PyPI Telethon (1.44.0)
> is only layer 227 and mis-parses current messages — it crash-loops the update
> loop and breaks `/pull`. See `vendor/BUILD.md` for how it's built and how to
> rebuild when Telegram bumps the layer again. Don't repin to a plain `telethon`
> until PyPI ships a layer-228+ release. On startup the bot checks Telegram's
> current layer and logs `⚠️ SCHEMA LAYER STALE …` if it has moved ahead of this
> build — watch `journalctl -u telegram-feed-bot` for that early warning.

2. Set up Telephon ([documentation](https://docs.telethon.dev/en/stable/basic/signing-in.html)). When logging in with a terminal, use info of a secondary account - it will forward messages from channels to your main account.

3. Enter your data in config.ini (you have received *api_id* and *api_hash* after Telephon installation; you can find your main account *user_id* and your secondary account *telephon_user_id* with @userinfobot)

## How to use

You can add a channel to feed by forwarding a message from it to the secondary account (*telephon_user_id*). If channel is already in the database, forwarding a message will remove it from feed. To check up channels list, send '/channels'.

You will receive new posts from listed channels in the same dialogue.

## Control commands

The preferred command surface is a dedicated **@BotFather bot**. Create one with
`/newbot` in [@BotFather](https://t.me/BotFather), put its token in
`config.ini` under `[telephon] bot_token`, and message the bot directly. It's a
normal, pinnable chat and its updates are delivered reliably. Commands are
owner-only: the sender's user id must be `user_id` or one of `command_user_ids`
(set the latter if you command from a *different* personal account than the one
the aggregator runs as). The account's own **Saved Messages** still works as a
fallback surface. Commands (case-insensitive):

- `/status` (or `/ping`) — confirm the bot is alive; shows uptime, version, target, and source channels
- `/channels` — list the configured source channels
- `/pull <time>` — backfill: fetch the last `<time>` of messages from **all** source feeds, clean/translate them, and post to the target channel. Time units: `m` (minutes), `h` (hours), `d` (days). Examples: `/pull 2h`, `/pull 90m`, `/pull 1d`.
- `/help` — list available commands

> `/pull` de-duplicates against what has already been forwarded during the current run, so overlapping windows won't double-post. Note that this de-dup memory resets when the service restarts.

## Filters & ad detection (config-driven, hot-reloaded)

All content rules live in `config.ini` — no code changes, and **no restart**: the
service notices when `config.ini` changes on disk and recompiles the rules on the
next message. In every list, `*` is a wildcard (any run of characters) and
everything else is matched literally, case-insensitively.

`[filters]` — clean up the text that *is* forwarded:

- `drop_lines` — remove any whole line that matches (best for promo footers)
- `remove_text` — delete just the matched substring, keeping the rest of the line
- `drop_lines_regex` / `remove_text_regex` — same, but the patterns are **real
  regex** (not wildcards), for precise cases the `*` wildcard can't express — e.g.
  `^\s*(\d+|one)\s+comments?\s*$` to strip a standalone "12 comments" line without
  touching real text that merely mentions "comment"

Backfilled `/pull` posts also get a `🕓 <original time>` footer (Israel local),
since a republished channel post is stamped at send time and can't be backdated.

`[ad_detection]` — drop the **entire** message as an ad/promo:

- `text_markers` — drop if the message text matches
- `button_markers` — drop if any inline-button label matches

```ini
[filters]
drop_lines =
    *Click here to respond to the article*
    New! *Aharon Yediot* App
    Download for iPhone:*
remove_text =
    https://t.me/yediotnews25

[ad_detection]
text_markers =
    תוכן שיווקי
    Here are today's top stories on Telegram
button_markers =
    לפרסום
```

Just edit `config.ini` and save — changes take effect on the next incoming post.
A malformed edit is logged and the previous rules are kept, so a typo never breaks
the running service. (Source channels and tokens still require a restart.)

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
