import logging
import asyncio
import configparser
import json
import os
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, events, functions, utils
from telethon.extensions import html
from telethon.tl import types as tl_types, alltlobjects as tl_alltlobjects

logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.INFO)

# --- Forward-compat schema shim ------------------------------------------------
# Telegram's live servers can send a core type with a NEWER constructor id than
# the published schema this Telethon was built against: adding a flag-gated field
# changes the type's CRC, but the wire layout is identical for instances that
# don't set the new field. Without a mapping, such an id raises TypeNotFoundError
# deep in the update loop (getDifference) and crashes the whole process — dropping
# a batch of live updates on every occurrence.
#
# Map each observed new id onto its known class so parsing succeeds. Verified by
# decoding real crash bytes: 0x1c32b11c parses cleanly as `channel` (layout
# identical to channel#d49f34c6) and lands exactly on the following object.
# Add ids here as Telegram rolls new ones out ahead of the public schema.
_FORWARD_COMPAT_CONSTRUCTORS = {
    0x1c32b11c: 'Channel',   # `channel` with a post-layer-228 field added
}
for _cid, _clsname in _FORWARD_COMPAT_CONSTRUCTORS.items():
    _cls = getattr(tl_types, _clsname, None)
    if _cls is not None:
        tl_alltlobjects.tlobjects.setdefault(_cid, _cls)
        logging.info(f"Forward-compat: mapped constructor {hex(_cid)} -> {_clsname}")

def is_ad(message):
    """True if the whole message should be dropped as an ad/promo, per the
    config-driven [ad_detection] markers (text markers + inline-button markers)."""
    text = getattr(message, 'text', '') or ''
    if any(rx.search(text) for rx in AD_TEXT_RES):
        return True

    rm = getattr(message, 'reply_markup', None)
    if rm is not None and hasattr(rm, 'rows'):
        for row in rm.rows:
            for button in getattr(row, 'buttons', []):
                btext = getattr(button, 'text', '') or ''
                if any(rx.search(btext) for rx in AD_BUTTON_RES):
                    return True
    return False

def filter_text(text):
    """Clean a message's (HTML) text using the config-driven [filters] rules.

    `drop_lines` patterns remove any whole line they match; `remove_text`
    patterns delete just the matched substring. In both, '*' is a wildcard and
    everything else is literal. Returns (cleaned_text, changed?)."""
    if not text:
        return text, False

    original_text = text.strip()

    kept = []
    for line in text.split('\n'):
        if any(rx.search(line) for rx in DROP_LINE_RES) or \
           any(rx.search(line) for rx in DROP_LINE_REGEX):
            continue
        for rx in REMOVE_TEXT_RES:
            line = rx.sub('', line)
        for rx in REMOVE_TEXT_REGEX:
            line = rx.sub('', line)
        kept.append(line)
    text = '\n'.join(kept)

    # Collapse blank lines any removal left behind
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    changed = text != original_text
    return text, changed

CONFIG_PATH = os.path.abspath('config.ini')
config = configparser.ConfigParser(interpolation=None)
config.read(CONFIG_PATH)


def _wildcard_to_regex(pattern):
    """Compile a user rule pattern to a regex. '*' matches any run of
    characters; every other character is treated literally. Case-insensitive."""
    return re.compile(re.escape(pattern).replace(r'\*', '.*'), re.IGNORECASE)


def _load_patterns(cfg, section, key):
    """Read a newline-separated list of wildcard patterns from [section].<key>."""
    raw = cfg.get(section, key, fallback='')
    out = []
    for line in raw.split('\n'):
        line = line.strip()
        if line and not line.startswith('#'):
            out.append(_wildcard_to_regex(line))
    return out


def _load_regex(cfg, section, key):
    """Read a newline-separated list of raw regex patterns from [section].<key>
    (not wildcard-escaped — full regex). Bad patterns are logged and skipped."""
    raw = cfg.get(section, key, fallback='')
    out = []
    for line in raw.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            out.append(re.compile(line, re.IGNORECASE))
        except re.error as e:
            logging.error(f"Bad regex in [{section}].{key}: {line!r} ({e})")
    return out


def _load_timezone(cfg):
    """Resolve [settings] display_timezone (IANA name, e.g. America/New_York) to a
    tzinfo for the /pull original-time footer. Falls back to ET, then UTC."""
    from zoneinfo import ZoneInfo
    name = (cfg.get('settings', 'display_timezone', fallback='') or '').strip() or 'America/New_York'
    try:
        return ZoneInfo(name)
    except Exception as e:
        logging.error(f"Invalid display_timezone {name!r} ({e}); falling back to America/New_York")
        try:
            return ZoneInfo('America/New_York')
        except Exception:
            return None


def _build_rules(cfg):
    """Compile every config-driven matching rule from a parsed config object."""
    return {
        'drop': _load_patterns(cfg, 'filters', 'drop_lines'),
        'remove': _load_patterns(cfg, 'filters', 'remove_text'),
        'drop_re': _load_regex(cfg, 'filters', 'drop_lines_regex'),
        'remove_re': _load_regex(cfg, 'filters', 'remove_text_regex'),
        'ad_text': _load_patterns(cfg, 'ad_detection', 'text_markers'),
        'ad_button': _load_patterns(cfg, 'ad_detection', 'button_markers'),
        'tz': _load_timezone(cfg),
        'poll_interval': max(30, cfg.getint('settings', 'poll_interval', fallback=120)),
    }


# Rule sets used by filter_text() and is_ad(). Rebuilt automatically whenever
# config.ini changes on disk (see _reload_config_if_changed) — no restart needed.
_rules = _build_rules(config)
DROP_LINE_RES = _rules['drop']
REMOVE_TEXT_RES = _rules['remove']
DROP_LINE_REGEX = _rules['drop_re']
REMOVE_TEXT_REGEX = _rules['remove_re']
AD_TEXT_RES = _rules['ad_text']
AD_BUTTON_RES = _rules['ad_button']
DISPLAY_TZ = _rules['tz']
POLL_INTERVAL = _rules['poll_interval']
try:
    _config_mtime = os.path.getmtime(CONFIG_PATH)
except OSError:
    _config_mtime = None


def _reload_config_if_changed():
    """Cheap per-message check: if config.ini's mtime changed, recompile the
    filter/ad rules from it. Bad edits are logged and the previous rules kept."""
    global DROP_LINE_RES, REMOVE_TEXT_RES, DROP_LINE_REGEX, REMOVE_TEXT_REGEX
    global AD_TEXT_RES, AD_BUTTON_RES, DISPLAY_TZ, POLL_INTERVAL, _config_mtime
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        return
    if mtime == _config_mtime:
        return
    _config_mtime = mtime  # update first so a broken file doesn't retry every message
    fresh = configparser.ConfigParser(interpolation=None)
    try:
        if not fresh.read(CONFIG_PATH):
            return
        r = _build_rules(fresh)
    except Exception as e:
        logging.error(f"config.ini changed but reload failed — keeping previous rules: {e}")
        return
    DROP_LINE_RES, REMOVE_TEXT_RES = r['drop'], r['remove']
    DROP_LINE_REGEX, REMOVE_TEXT_REGEX = r['drop_re'], r['remove_re']
    AD_TEXT_RES, AD_BUTTON_RES = r['ad_text'], r['ad_button']
    DISPLAY_TZ = r['tz']
    POLL_INTERVAL = r['poll_interval']
    logging.info(
        f"Rules reloaded from config.ini: {len(DROP_LINE_RES)} drop_lines, "
        f"{len(REMOVE_TEXT_RES)} remove_text, {len(DROP_LINE_REGEX)} drop_lines_regex, "
        f"{len(REMOVE_TEXT_REGEX)} remove_text_regex, {len(AD_TEXT_RES)} ad-text, "
        f"{len(AD_BUTTON_RES)} ad-button markers"
    )

username = config['telephon']['username']
api_id = config['telephon']['api_id']
api_hash = config['telephon']['api_hash']
# Optional: a @BotFather bot token. If set, the bot exposes the control
# commands (/status, /pull, …) in a normal chat with that bot — far more
# discoverable than the account's Saved Messages. Empty = Saved Messages only.
BOT_TOKEN = config.get('telephon', 'bot_token', fallback='').strip()

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
# Accounts allowed to issue commands to the command bot. The bot sees the
# SENDER's user id, which — if you command from a different personal account
# than the one the aggregator runs as — is NOT OWNER_ID. List those ids in
# [telephon] command_user_ids (comma/space separated). OWNER_ID is always allowed.
COMMAND_OWNER_IDS = {OWNER_ID} | {
    int(x) for x in re.findall(r'\d+', config.get('telephon', 'command_user_ids', fallback=''))
}
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

# Ingestion is POLL-BASED (see poll_sources): we read each source's history on a
# timer instead of consuming Telegram's push-update stream. History reads are a
# far more stable API surface — the update stream's schema churn is what caused
# repeated TypeNotFoundError process crashes. With a command bot configured, the
# user client doesn't need updates at all, so the whole subsystem is turned off.
# (Without a bot token, updates stay on so Saved Messages commands still work.)
client = TelegramClient(username, api_id, api_hash, system_version="4.16.30-vxCUSTOM_STRING",
                        receive_updates=not BOT_TOKEN)

# Second, optional client: a Bot-API bot used purely as the command surface.
# It shares this process/event-loop with the user client, so its command
# handlers can drive the same read/translate/republish pipeline.
bot = TelegramClient('command_bot', api_id, api_hash) if BOT_TOKEN else None

# We keep track of the messages we've already forwarded to prevent duplicates
# from things like edits, grouped media (albums), or duplicate events.
forwarded_message_ids = set()

def get_message_link(chat_id, from_chat, message_id):
    username = getattr(from_chat, 'username', None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    else:
        clean_id = str(chat_id).replace('-100', '')
        return f"https://t.me/c/{clean_id}/{message_id}"

def _original_time_footer(original_date):
    """A small footer line with the post's ORIGINAL time in the configured
    display timezone ([settings] display_timezone, default ET), used on backfilled
    /pull posts — Telegram stamps the republished post at send time and a channel
    post can't be backdated, so we surface the real time in the text."""
    if not original_date:
        return ""
    dt = original_date
    if DISPLAY_TZ is not None:
        try:
            dt = dt.astimezone(DISPLAY_TZ)
        except Exception:
            pass
    # %Z yields the zone abbreviation (EDT/EST/CDT/CST/UTC) so it's unambiguous.
    stamp = dt.strftime('%d %b %Y, %H:%M %Z').strip()
    return f"\n🕓 {stamp}"


async def process_single_message(message, from_chat, chat_id, original_date=None):
    """Clean, translate, and send one (non-album) message to the target channel.

    Shared by the live NewMessage handler and the /pull backfill. When
    original_date is given (backfill), the post's original time is appended as a
    footer. Returns True if something was sent, False if skipped."""
    _reload_config_if_changed()
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
        new_text = f"{final_html_text}\n\n<a href='{msg_url}'>{chat_name}</a>{_original_time_footer(original_date)}"
        await client.send_message(TARGET_CHANNEL, message=new_text, file=message.media, parse_mode='html', link_preview=False)
    else:
        logging.info(f"Forwarding NEW distinct message from '{chat_name}' (Msg ID: {message.id})")
        await client.forward_messages(TARGET_CHANNEL, message)
    return True


async def process_album(messages, from_chat, chat_id, original_date=None):
    """Clean, translate, and send a grouped-media album to the target channel.

    Shared by the live Album handler and the /pull backfill. When original_date is
    given (backfill), the album's original time is appended. Returns True if sent."""
    _reload_config_if_changed()
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
        new_caption_final = f"{new_caption}\n\n<a href='{msg_url}'>{chat_name}</a>{_original_time_footer(original_date)}"
        await client.send_message(TARGET_CHANNEL, message=new_caption_final, file=list(messages), parse_mode='html', link_preview=False)
    else:
        logging.info(f"Forwarding NEW distinct album from '{chat_name}' (Group ID: {grouped_id})")
        await client.forward_messages(TARGET_CHANNEL, list(messages))
    return True


# ---------------------------------------------------------------------------
# Ingestion engine: history polling.
#
# Every POLL_INTERVAL seconds, ask each source for "messages newer than the last
# id I processed" (one cheap getHistory request per source) and run them through
# the same clean/translate/republish pipeline as /pull. Compared to the previous
# push-update handlers this is dramatically more robust: no dependency on
# Telegram's update-stream schema (whose churn crashed the process), per-source
# errors just skip a cycle, and progress is persisted to disk so restarts never
# double-post or lose position.
# ---------------------------------------------------------------------------

STATE_PATH = os.path.join(SCRIPT_DIR, 'state.json')
POLL_GRACE_SECONDS = 30   # let in-flight albums finish uploading before ingesting
POLL_MAX_PER_CYCLE = 200  # safety cap per source per cycle
LAST_POLL_TIME = None     # for /status


def _load_state():
    """last-processed message id per source, persisted across restarts."""
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=0)
    os.replace(tmp, STATE_PATH)


async def _poll_one_source(source, state, entities):
    """Fetch and process everything new in one source. Updates state on success."""
    key = str(source)
    ent = entities.get(key)
    if ent is None:
        ent = await client.get_entity(source)
        entities[key] = ent
    chat_id = utils.get_peer_id(ent)

    last_id = state.get(key)
    if last_id is None:
        # First time we see this source: baseline at its newest message and start
        # forwarding from the NEXT one (use /pull for history).
        newest = await client.get_messages(ent, limit=1)
        state[key] = newest[0].id if newest else 0
        _save_state(state)
        logging.info(f"poll: baseline {key} at msg id {state[key]}")
        return 0

    collected = []
    async for m in client.iter_messages(ent, min_id=last_id, limit=POLL_MAX_PER_CYCLE):
        if getattr(m, 'action', None):  # service messages (pins/joins)
            continue
        collected.append(m)
    if not collected:
        return 0

    # Grace window: defer very fresh messages (and any album a fresh item belongs
    # to) until next cycle, so multi-part albums are always ingested whole.
    grace = datetime.now(timezone.utc) - timedelta(seconds=POLL_GRACE_SECONDS)
    defer_gids = {m.grouped_id for m in collected if m.date > grace and m.grouped_id}
    collected = [m for m in collected
                 if m.date <= grace and (not m.grouped_id or m.grouped_id not in defer_gids)]
    if not collected:
        return 0

    collected.reverse()  # oldest-first, chronological in the target channel

    albums = {}
    for m in collected:
        if m.grouped_id:
            albums.setdefault(m.grouped_id, []).append(m)

    sent = 0
    seen_groups = set()
    for m in collected:
        gid = m.grouped_id
        if gid:
            if gid in seen_groups:
                continue
            seen_groups.add(gid)
            ok = await process_album(albums[gid], ent, chat_id)
        else:
            ok = await process_single_message(m, ent, chat_id)
        if ok:
            sent += 1
            await asyncio.sleep(1)  # gentle pacing

    state[key] = max(m.id for m in collected)
    _save_state(state)
    return sent


async def poll_sources():
    """Main ingestion loop. A failure in one source never affects the others or
    the process — it's logged and retried next cycle."""
    global LAST_POLL_TIME
    state = _load_state()
    entities = {}
    logging.info(f"Polling {len(SOURCE_CHATS)} sources every {POLL_INTERVAL}s "
                 f"(state: {STATE_PATH})")
    while True:
        for source in SOURCE_CHATS:
            try:
                n = await _poll_one_source(source, state, entities)
                if n:
                    logging.info(f"poll: {source} -> {n} new post(s)")
            except Exception as e:
                entities.pop(str(source), None)  # re-resolve next time
                logging.error(f"poll: {source!r} failed this cycle (will retry): {e}")
        LAST_POLL_TIME = datetime.now(timezone.utc)
        await asyncio.sleep(POLL_INTERVAL)


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
                    ok = await process_album(albums[gid], entity, chat_id,
                                             original_date=albums[gid][0].date)
                else:
                    ok = await process_single_message(msg, entity, chat_id,
                                                      original_date=msg.date)
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

# Case-insensitive so "/Help" (phone auto-capitalisation) matches "/help".
COMMAND_RE = re.compile(r'^/(start|status|ping|alive|channels|pull|help)\b', re.IGNORECASE)


async def run_command(event):
    """Execute a control command. Reached via two owner-only front doors:
    the command bot (if configured) or the account's own Saved Messages."""
    cmd = event.raw_text.split()[0].lstrip('/').lower()
    try:
        if cmd in ('status', 'ping', 'alive'):
            sources = ', '.join(str(s) for s in SOURCE_CHATS) or '(none)'
            if LAST_POLL_TIME:
                ago = int((datetime.now(timezone.utc) - LAST_POLL_TIME).total_seconds())
                poll_line = f"🔁 Poll: every {POLL_INTERVAL}s (last cycle {ago}s ago)"
            else:
                poll_line = f"🔁 Poll: every {POLL_INTERVAL}s (first cycle pending)"
            await event.reply(
                "✅ **Bot is alive**\n"
                f"⏱ Uptime: {_fmt_uptime()}\n"
                f"🔀 Version: {_git_version()}\n"
                f"{poll_line}\n"
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
        elif cmd in ('help', 'start'):
            await event.reply(
                "🤖 **Commands** (send them right here):\n"
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


@client.on(events.NewMessage(pattern=COMMAND_RE))
async def _saved_messages_commands(event):
    """Owner-only: commands typed in the user account's Saved Messages (self-chat)."""
    if not (event.is_private and event.chat_id == OWNER_ID):
        return
    await run_command(event)


def _register_bot_commands():
    """Owner-only: commands sent to the @BotFather command bot, if configured."""
    @bot.on(events.NewMessage(pattern=COMMAND_RE))
    async def _bot_commands(event):
        if event.sender_id not in COMMAND_OWNER_IDS:
            logging.warning(f"BOT: ignoring command from non-owner sender {event.sender_id}")
            return
        await run_command(event)


# Source of Telegram's current schema (same one vendor/BUILD.md builds from).
TL_SCHEMA_URL = ('https://raw.githubusercontent.com/telegramdesktop/tdesktop/'
                 'dev/Telegram/SourceFiles/mtproto/scheme/api.tl')


async def check_schema_layer():
    """Best-effort startup check: warn if Telegram's current schema layer is
    newer than the layer this Telethon build targets — an early heads-up that
    /pull and live parsing may soon break. Rebuild per vendor/BUILD.md. Never
    blocks startup and stays silent if the network is unavailable."""
    try:
        from telethon.tl import alltlobjects
        our_layer = int(alltlobjects.LAYER)
    except Exception:
        return

    def _fetch_current_layer():
        req = urllib.request.Request(TL_SCHEMA_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read().decode('utf-8', 'replace')
        layers = [int(n) for n in re.findall(r'//\s*LAYER\s+(\d+)', data)]
        return max(layers) if layers else None

    try:
        current = await asyncio.to_thread(_fetch_current_layer)
    except Exception as e:
        logging.info(f"Schema-layer check skipped (could not fetch current layer): {e}")
        return
    if not current:
        return
    if current > our_layer:
        logging.warning(
            f"⚠️ SCHEMA LAYER STALE: this Telethon targets layer {our_layer}, but "
            f"Telegram's current layer is {current}. History reads (/pull) and live "
            f"message parsing may break soon — rebuild the vendored Telethon "
            f"(see vendor/BUILD.md)."
        )
    else:
        logging.info(f"Schema layer OK (build {our_layer}, current {current}).")


async def _run_updates_forever(c, name):
    """Keep a client's update loop alive. A parse error in the update stream
    (Telegram schema churn) costs only that batch — it must never kill the
    process, which is exactly what run_until_disconnected() does by re-raising."""
    while True:
        try:
            await c.run_until_disconnected()
            return  # genuine disconnect (shutdown)
        except Exception as e:
            logging.error(f"{name}: update-loop error (recovering): {e}")
            await asyncio.sleep(5)


async def _main():
    await client.start()
    coros = [poll_sources()]
    if bot is not None:
        _register_bot_commands()
        await bot.start(bot_token=BOT_TOKEN)
        me = await bot.get_me()
        logging.info(f"Command bot online as @{me.username}")
        coros.append(_run_updates_forever(bot, 'command-bot'))
    else:
        # No command bot: the user client keeps its update loop for Saved Messages commands.
        coros.append(_run_updates_forever(client, 'client'))
    logging.info("Bot started successfully (poll-based ingestion).")
    asyncio.create_task(poll_rss_feeds())
    asyncio.create_task(check_schema_layer())
    await asyncio.gather(*coros)


if __name__ == '__main__':
    asyncio.run(_main())
