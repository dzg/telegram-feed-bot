import logging
import asyncio
import configparser
import re
import urllib.request
import xml.etree.ElementTree as ET
from telethon import TelegramClient, events, functions
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

@client.on(events.NewMessage())
async def forward_unique_messages(event):
    """
    Triggers on all new messages. Checks if it's from a target chat.
    """
    try:
        # Ignore messages that are part of an album, they are handled by events.Album
        if event.message.grouped_id:
            return

        chat_id = event.chat_id
        from_chat = await event.get_chat()
        chat_name = getattr(from_chat, 'title', str(chat_id))
        
        # Check if it matches our desired channels list
        if is_source_chat(chat_id, from_chat):
            
            # Mark the original message as read
            try:
                await client.send_read_acknowledge(from_chat, message=event.message)
            except Exception as e:
                logging.debug(f"Failed to mark message {event.message.id} as read: {e}")
            
            # Create a unique signature for this message so we don't double-forward it
            # Using the chat ID and the message ID ensures uniqueness across channels.
            msg_signature = f"{chat_id}_{event.message.id}"
            
            # If we haven't seen this message ID before...
            if msg_signature not in forwarded_message_ids:
                forwarded_message_ids.add(msg_signature)
                
                if is_ad(event.message):
                    logging.info(f"Dropped AD message from '{chat_name}' (Msg ID: {event.message.id})")
                    return

                # First, get the raw HTML string representing the original message (with its original formatting)
                original_html = html.unparse(getattr(event.message, 'message', ''), getattr(event.message, 'entities', []))
                final_html_text, changed = filter_text(original_html)
                
                # Attempt to translate the text if it's not empty
                if final_html_text:
                    try:
                        translation = await client(functions.messages.TranslateTextRequest(
                            peer=from_chat,
                            id=[event.message.id],
                            to_lang='en'
                        ))
                        if translation and translation.result:
                            # Unparse the translated text to preserve translated formatting
                            translated_res = translation.result[0]
                            translated_html = html.unparse(translated_res.text, getattr(translated_res, 'entities', []))
                            final_html_text, _ = filter_text(translated_html)
                            changed = True
                            logging.info(f"Successfully translated message {event.message.id} from {chat_name}")
                    except Exception as translate_err:
                        logging.error(f"Failed to translate message {event.message.id}: {translate_err}")

                if changed or getattr(event.message, 'text', ''):
                    # We send it as a new message instead of forwarding so we can attach the translated text
                    logging.info(f"Sending TRANSLATED/MODIFIED message from '{chat_name}' (Msg ID: {event.message.id})")
                    msg_url = get_message_link(chat_id, from_chat, event.message.id)
                    
                    # Instead of HTML escaping final_text here, it's already HTML escaped and parsed via `html.unparse`
                    new_text = f"{final_html_text}\n\n<a href='{msg_url}'>{chat_name}</a>"
                    await client.send_message(TARGET_CHANNEL, message=new_text, file=event.message.media, parse_mode='html', link_preview=False)
                else:
                    logging.info(f"Forwarding NEW distinct message from '{chat_name}' (Msg ID: {event.message.id})")
                    await client.forward_messages(TARGET_CHANNEL, event.message)
                
            else:
                logging.debug(f"Ignored duplicate event for message {event.message.id} in '{chat_name}'")

    except Exception as e:
        logging.error(f"Error forwarding message: {e}")

@client.on(events.Album())
async def forward_unique_albums(event):
    """
    Triggers on grouped messages (albums).
    """
    try:
        chat_id = event.chat_id
        from_chat = await event.get_chat()
        chat_name = getattr(from_chat, 'title', str(chat_id))
        
        # Check if it matches our desired channels list
        if is_source_chat(chat_id, from_chat):
            # We use the grouped_id to uniquely identify this album
            # Since event.grouped_id might not exist directly on the Album object depending on the Telethon version, we can check the first message safely.
            grouped_id = getattr(event, 'grouped_id', event.messages[0].grouped_id) if event.messages else None
            
            # Mark the album messages as read (using the max_id parameter)
            try:
                if event.messages:
                    await client.send_read_acknowledge(from_chat, max_id=event.messages[-1].id)
            except Exception as e:
                logging.debug(f"Failed to mark album {grouped_id} as read: {e}")
            
            if not grouped_id:
                return

            msg_signature = f"{chat_id}_album_{grouped_id}"
            
            if msg_signature not in forwarded_message_ids:
                forwarded_message_ids.add(msg_signature)
                
                if any(is_ad(m) for m in event.messages):
                    logging.info(f"Dropped AD album from '{chat_name}' (Group ID: {grouped_id})")
                    return
                
                changed_any = False
                new_caption = ""
                message_ids_to_translate = []
                
                for m in event.messages:
                    if getattr(m, 'text', None):
                        message_ids_to_translate.append(m.id)

                translated_htmls = {}
                if message_ids_to_translate:
                    try:
                        translation = await client(functions.messages.TranslateTextRequest(
                            peer=from_chat,
                            id=message_ids_to_translate,
                            to_lang='en'
                        ))
                        for i, res in enumerate(translation.result):
                            translated_htmls[message_ids_to_translate[i]] = html.unparse(res.text, getattr(res, 'entities', []))
                    except Exception as e:
                        logging.error(f"Failed to translate album {grouped_id}: {e}")

                for m in event.messages:
                    original_html = html.unparse(getattr(m, 'message', ''), getattr(m, 'entities', []))
                    html_to_process = translated_htmls.get(m.id, original_html)
                    
                    if not html_to_process:
                        continue
                        
                    filtered_html, changed = filter_text(html_to_process)
                    # Any translation implies we modified it
                    if changed or m.id in translated_htmls:
                        changed_any = True
                        
                    if not new_caption and filtered_html:
                        new_caption = filtered_html
                
                if changed_any:
                    logging.info(f"Sending TRANSLATED/MODIFIED album from '{chat_name}' (Group ID: {grouped_id})")
                    first_msg_id = event.messages[0].id if event.messages else grouped_id
                    msg_url = get_message_link(chat_id, from_chat, first_msg_id)
                    new_caption_final = f"{new_caption}\n\n<a href='{msg_url}'>{chat_name}</a>"
                    await client.send_message(TARGET_CHANNEL, message=new_caption_final, file=event.messages, parse_mode='html', link_preview=False)
                else:
                    logging.info(f"Forwarding NEW distinct album from '{chat_name}' (Group ID: {grouped_id})")
                    await client.forward_messages(TARGET_CHANNEL, event.messages)
                
            else:
                logging.debug(f"Ignored duplicate event for album {grouped_id} in '{chat_name}'")

    except Exception as e:
        logging.error(f"Error forwarding album: {e}")

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

if __name__ == '__main__':
    client.start()
    logging.info("Bot started successfully. Waiting for new posts (duplicate protection active)...")
    client.loop.create_task(poll_rss_feeds())
    client.run_until_disconnected()
