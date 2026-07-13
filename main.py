import logging
import asyncio
import configparser
import os
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, events, functions, utils
from telethon.extensions import html

logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.INFO)

def is_ad(message):
    text = getattr(message, 'text', '') or ''
    if 'תוכן שיווקי' in text or "Here are today's top stories on Telegram" in text:
        return True
    
    if getattr(message, 'reply_markup', None) and hasattr(message.reply_markup, 'rows'):
        for row in message.reply_markup.rows:
            for button in getattr(row, 'buttons', []):
                if 'לפרסום' in getattr(button, 'text', ''):
                    return True
    return False

def filter_text(text):
    if not text:
        return text, False
    
    original_text = text.strip()
    text = text.replace('https://t.me/yediotnews25', '')
    text = re.sub(r'(?m)^.*https://abualiexpress\.com/.*', '', text)
    
    # Remove the specific phrase and its surrounding HTML link tags if present
    text = re.sub(r'(?i)<a[^>]*>\s*Click here to respond to the article\s*</a>', '', text)
    text = re.sub(r'(?i)Click here to respond to the article', '', text)
    
    text = text.strip()
    
    changed = text != original_text
    return text, changed

config = configparser.ConfigParser()
config.read('config.ini')

username = config['telephon']['username']
api_id = config['telephon']['api_id']
api_hash = config['telephon']['api_hash']

TARGET_CHANNEL = config.get('settings', 'target_channel', fallback='https://t.me/newzzzzzil')

raw_source_chats = config.get('settings', 'source_chats', fallback='').strip().split('\n')
SOURCE_CHATS = []
for chat in raw_source_chats:
    chat = chat.strip()
    if not chat:
        continue
    try:
        SOURCE_CHATS.append(int(chat))
    except ValueError:
        SOURCE_CHATS.append(chat)

raw_rss_feeds = config.get('settings', 'rss_feeds', fallback='').strip().split('\n')
RSS_FEEDS = [feed.strip() for feed in raw_rss_feeds if feed.strip()]

# The bot runs AS this account (its own user id). Control commands are honored
# only in this account's Saved Messages (self-chat), which only the owner can
# post to — so that chat is an inherently private, owner-only command surface.
OWNER_ID = int(config.get('telephon', 'user_id'))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_START_TIME = datetime.now(timezone.utc)


def _run(cmd):
    """Run a shell command in the project dir, return (returncode, combined_output)."""
    try:
        r = subprocess.run(cmd, cwd=SCRIPT_DIR, capture_output=True, text=True, timeout=120)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return -1, str(e)


def _fmt_uptime():
    secs = int((datetime.now(timezone.utc) - BOT_START_TIME).total_seconds())
    d, secs = divmod(secs, 86400)
    h, secs = divmod(secs, 3600)
    m, _ = divmod(secs, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


def _git_version():
    rc_b, branch = _run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])
    rc_s, sha = _run(['git', 'rev-parse', '--short', 'HEAD'])
    return f"{branch if rc_b == 0 else '?'} @ {sha if rc_s == 0 else '?'}"

client = TelegramClient(username, api_id, api_hash, system_version="4.16.30-vxCUSTOM_STRING")

# We keep track of the messages we've already forwarded to prevent duplicates
# from things like edits, grouped media (albums), or duplicate events.
forwarded_message_ids = set()

def is_source_chat(chat_id, from_chat):
    chat_name = getattr(from_chat, 'title', str(chat_id))
    if chat_id in SOURCE_CHATS or chat_name in SOURCE_CHATS:
        return True
    
    username = getattr(from_chat, 'username', None)
    if username:
        formats = [username, f"@{username}", f"t.me/{username}", f"https://t.me/{username}"]
        source_strs = [s.lower() for s in SOURCE_CHATS if isinstance(s, str)]
        return any(f.lower() in source_strs for f in formats)
    return False

def get_message_link(chat_id, from_chat, message_id):
    username = getattr(from_chat, 'username', None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    else:
        clean_id = str(chat_id).replace('-100', '')
        return f"https://t.me/c/{clean_id}/{message_id}"

async def process_single_message(message, from_chat, chat_id):
    """Clean, translate, and send one (non-album) message to the target channel.

    Shared by the live NewMessage handler and the /pull backfill. Returns True if
    something was sent, False if skipped (duplicate/ad/empty)."""
    chat_name = getattr(from_chat, 'title', str(chat_id))
    msg_signature = f"{chat_id}_{message.id}"
    if msg_signature in forwarded_message_ids:
        logging.debug(f"Skipping already-seen message {message.id} in '{chat_name}'")
        return False
    forwarded_message_ids.add(msg_signature)

    if is_ad(message):
        logging.info(f"Dropped AD message from '{chat_name}' (Msg ID: {message.id})")
        return False

    # Raw HTML of the original message (preserves formatting)
    original_html = html.unparse(getattr(message, 'message', ''), getattr(message, 'entities', []))
    final_html_text, changed = filter_text(original_html)

    if final_html_text:
        try:
            translation = await client(functions.messages.TranslateTextRequest(
                peer=from_chat, id=[message.id], to_lang='en'))
            if translation and translation.result:
                translated_res = translation.result[0]
                translated_html = html.unparse(translated_res.text, getattr(translated_res, 'entities', []))
                final_html_text, _ = filter_text(translated_html)
                changed = True
                logging.info(f"Successfully translated message {message.id} from {chat_name}")
        except Exception as translate_err:
            logging.error(f"Failed to translate message {message.id}: {translate_err}")

    if changed or getattr(message, 'text', ''):
        logging.info(f"Sending TRANSLATED/MODIFIED message from '{chat_name}' (Msg ID: {message.id})")
        msg_url = get_message_link(chat_id, from_chat, message.id)
        new_text = f"{final_html_text}\n\n<a href='{msg_url}'>{chat_name}</a>"
        await client.send_message(TARGET_CHANNEL, message=new_text, file=message.media, parse_mode='html', link_preview=False)
    else:
        logging.info(f"Forwarding NEW distinct message from '{chat_name}' (Msg ID: {message.id})")
        await client.forward_messages(TARGET_CHANNEL, message)
    return True


async def process_album(messages, from_chat, chat_id):
    """Clean, translate, and send a grouped-media album to the target channel.

    Shared by the live Album handler and the /pull backfill. Returns True if sent."""
    chat_name = getattr(from_chat, 'title', str(chat_id))
    if not messages:
        return False
    grouped_id = getattr(messages[0], 'grouped_id', None)
    if not grouped_id:
        return False

    msg_signature = f"{chat_id}_album_{grouped_id}"
    if msg_signature in forwarded_message_ids:
        logging.debug(f"Skipping already-seen album {grouped_id} in '{chat_name}'")
        return False
    forwarded_message_ids.add(msg_signature)

    if any(is_ad(m) for m in messages):
        logging.info(f"Dropped AD album from '{chat_name}' (Group ID: {grouped_id})")
        return False

    changed_any = False
    new_caption = ""
    message_ids_to_translate = [m.id for m in messages if getattr(m, 'text', None)]

    translated_htmls = {}
    if message_ids_to_translate:
        try:
            translation = await client(functions.messages.TranslateTextRequest(
                peer=from_chat, id=message_ids_to_translate, to_lang='en'))
            for i, res in enumerate(translation.result):
                translated_htmls[message_ids_to_translate[i]] = html.unparse(res.text, getattr(res, 'entities', []))
        except Exception as e:
            logging.error(f"Failed to translate album {grouped_id}: {e}")

    for m in messages:
        original_html = html.unparse(getattr(m, 'message', ''), getattr(m, 'entities', []))
        html_to_process = translated_htmls.get(m.id, original_html)
        if not html_to_process:
            continue
        filtered_html, changed = filter_text(html_to_process)
        if changed or m.id in translated_htmls:
            changed_any = True
        if not new_caption and filtered_html:
            new_caption = filtered_html

    if changed_any:
        logging.info(f"Sending TRANSLATED/MODIFIED album from '{chat_name}' (Group ID: {grouped_id})")
        msg_url = get_message_link(chat_id, from_chat, messages[0].id)
        new_caption_final = f"{new_caption}\n\n<a href='{msg_url}'>{chat_name}</a>"
        await client.send_message(TARGET_CHANNEL, message=new_caption_final, file=list(messages), parse_mode='html', link_preview=False)
    else:
        logging.info(f"Forwarding NEW distinct album from '{chat_name}' (Group ID: {grouped_id})")
        await client.forward_messages(TARGET_CHANNEL, list(messages))
    return True


@client.on(events.NewMessage())
async def forward_unique_messages(event):
    """Triggers on all new messages; forwards those from a source channel."""
    try:
        # Albums are handled by the events.Album handler below
        if event.message.grouped_id:
            return

        chat_id = event.chat_id
        from_chat = await event.get_chat()
        if not is_source_chat(chat_id, from_chat):
            return

        try:
            await client.send_read_acknowledge(from_chat, message=event.message)
        except Exception as e:
            logging.debug(f"Failed to mark message {event.message.id} as read: {e}")

        await process_single_message(event.message, from_chat, chat_id)
    except Exception as e:
        logging.error(f"Error forwarding message: {e}")


@client.on(events.Album())
async def forward_unique_albums(event):
    """Triggers on grouped messages (albums) from a source channel."""
    try:
        chat_id = event.chat_id
        from_chat = await event.get_chat()
        if not is_source_chat(chat_id, from_chat):
            return

        try:
            if event.messages:
                await client.send_read_acknowledge(from_chat, max_id=event.messages[-1].id)
        except Exception as e:
            logging.debug(f"Failed to mark album as read: {e}")

        await process_album(event.messages, from_chat, chat_id)
    except Exception as e:
        logging.error(f"Error forwarding album: {e}")


def parse_duration(text):
    """Parse '2h', '90m', '1d' -> number of seconds. Returns None if invalid."""
    m = re.match(r'^\s*(\d+)\s*([mhd])\s*$', text, re.IGNORECASE)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    if n <= 0:
        return None
    return n * {'m': 60, 'h': 3600, 'd': 86400}[unit]


async def backfill_pull(seconds, max_per_source=200):
    """Fetch every message newer than `seconds` ago from each source channel and
    run it through the same clean/translate/republish pipeline. Albums are
    regrouped so they post as a single album. Returns (total_sent, per_source)."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    total_sent = 0
    per_source = {}

    for source in SOURCE_CHATS:
        try:
            entity = await client.get_entity(source)
        except Exception as e:
            logging.error(f"/pull: could not resolve source {source!r}: {e}")
            per_source[str(source)] = f"⚠️ unresolved"
            continue

        chat_id = utils.get_peer_id(entity)
        chat_name = getattr(entity, 'title', str(source))

        # iter_messages yields newest-first; collect until we pass the cutoff
        collected = []
        try:
            async for msg in client.iter_messages(entity, limit=max_per_source):
                if msg.date < cutoff:
                    break
                if getattr(msg, 'action', None):  # skip service messages (joins/pins)
                    continue
                collected.append(msg)
        except Exception as e:
            logging.error(f"/pull: error reading {chat_name}: {e}")
            per_source[chat_name] = f"⚠️ read error"
            continue

        collected.reverse()  # oldest-first so the target channel reads chronologically

        # Regroup album items by grouped_id
        albums = {}
        for msg in collected:
            gid = getattr(msg, 'grouped_id', None)
            if gid:
                albums.setdefault(gid, []).append(msg)

        sent_here = 0
        seen_groups = set()
        for msg in collected:
            try:
                gid = getattr(msg, 'grouped_id', None)
                if gid:
                    if gid in seen_groups:
                        continue
                    seen_groups.add(gid)
                    ok = await process_album(albums[gid], entity, chat_id)
                else:
                    ok = await process_single_message(msg, entity, chat_id)
                if ok:
                    sent_here += 1
                    total_sent += 1
                    await asyncio.sleep(1)  # gentle pacing to avoid flood limits
            except Exception as e:
                logging.error(f"/pull: failed on message {getattr(msg, 'id', '?')} in {chat_name}: {e}")

        per_source[chat_name] = sent_here
        logging.info(f"/pull: {chat_name} -> {sent_here} sent")

    return total_sent, per_source

async def poll_rss_feeds():
    seen_rss_links = set()
    first_run = True
    
    while True:
        for feed_url in RSS_FEEDS:
            try:
                def fetch_feed(url):
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=10) as response:
                        return response.read()
                
                xml_data = await asyncio.to_thread(fetch_feed, feed_url)
                root = ET.fromstring(xml_data)
                
                # Iterate in reverse to send oldest first if there are multiple new ones
                items = root.findall('.//item')
                for item in reversed(items):
                    link_elem = item.find('link')
                    if link_elem is None or not link_elem.text:
                        continue
                        
                    link = link_elem.text.strip()
                    if link not in seen_rss_links:
                        if not first_run:
                            logging.info(f"Forwarding new RSS link: {link}")
                            text = f"New post from X:\n{link}"
                            await client.send_message(TARGET_CHANNEL, message=text)
                        seen_rss_links.add(link)
                        
            except Exception as e:
                logging.error(f"Error polling RSS feed {feed_url}: {e}")
        
        first_run = False
        # Poll every 5 minutes
        await asyncio.sleep(300)

@client.on(events.NewMessage(pattern=r'^/(status|ping|alive|channels|pull|help)\b'))
async def handle_owner_commands(event):
    """Control commands, sent in the bot account's own Saved Messages (self-chat)."""
    # Saved Messages is the only chat whose id equals our own account id, and
    # only we can post there — this is both the owner check and the scope filter.
    if not (event.is_private and event.chat_id == OWNER_ID):
        return
    cmd = event.raw_text.split()[0].lstrip('/').lower()
    try:
        if cmd in ('status', 'ping', 'alive'):
            sources = ', '.join(str(s) for s in SOURCE_CHATS) or '(none)'
            await event.reply(
                "✅ **Bot is alive**\n"
                f"⏱ Uptime: {_fmt_uptime()}\n"
                f"🔀 Version: {_git_version()}\n"
                f"🎯 Target: {TARGET_CHANNEL}\n"
                f"📡 Sources ({len(SOURCE_CHATS)}): {sources}"
            )
        elif cmd == 'channels':
            listed = '\n'.join(f"• {s}" for s in SOURCE_CHATS) or '(none)'
            await event.reply(f"📡 **Source channels ({len(SOURCE_CHATS)}):**\n{listed}")
        elif cmd == 'pull':
            parts = event.raw_text.split()
            arg = parts[1] if len(parts) > 1 else ''
            seconds = parse_duration(arg)
            if not seconds:
                await event.reply(
                    "⚠️ Usage: `/pull 2h` or `/pull 1d` (units: `m` minutes, `h` hours, `d` days)."
                )
                return
            await event.reply(
                f"⏳ Pulling the last **{arg}** from {len(SOURCE_CHATS)} feed(s), "
                "cleaning/translating, and posting to the channel… this can take a while."
            )
            total, per_source = await backfill_pull(seconds)
            summary = '\n'.join(f"• {k}: {v}" for k, v in per_source.items()) or '(no sources)'
            await event.reply(f"✅ Backfill complete — **{total}** item(s) posted.\n{summary}")
        elif cmd == 'help':
            await event.reply(
                "🤖 **Commands** (send here, in Saved Messages):\n"
                "/status — alive check, uptime, version, sources\n"
                "/channels — list source channels\n"
                "/pull `<time>` — backfill the last e.g. `2h` or `1d` from all feeds\n"
                "/help — this message"
            )
    except Exception as e:
        logging.error(f"Command '/{cmd}' failed: {e}")
        try:
            await event.reply(f"⚠️ Command error: {e}")
        except Exception:
            pass


if __name__ == '__main__':
    client.start()
    logging.info("Bot started successfully. Waiting for new posts (duplicate protection active)...")
    client.loop.create_task(poll_rss_feeds())
    client.run_until_disconnected()
