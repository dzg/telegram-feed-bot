import asyncio
import configparser
from telethon import TelegramClient

config = configparser.ConfigParser()
config.read('config.ini')

username = config['telephon']['username']
api_id = config['telephon']['api_id']
api_hash = config['telephon']['api_hash']

client = TelegramClient(username + "_test", api_id, api_hash)

async def main():
    await client.start()
    print("Client started")
    
    channel = 'beholdisraelchannel'
    print(f"Fetching latest message from {channel}...")
    
    # Get the entity
    entity = await client.get_entity(channel)
    print(f"Channel found: {getattr(entity, 'title', 'Unknown')} (ID: {entity.id}, Username: {getattr(entity, 'username', 'None')})")
    
    # Check against our config parsing logic
    raw_source_chats = config.get('settings', 'source_chats', fallback='').strip().split('\n')
    SOURCE_CHATS = [c.strip() for c in raw_source_chats if c.strip()]
    
    print(f"SOURCE_CHATS parsed from config: {SOURCE_CHATS}")
    
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

    print(f"Match test result: {is_source_chat(entity.id, entity)}")
    
    # Get last 1 message
    async for message in client.iter_messages(entity, limit=1):
        print("\n--- LATEST POST ---")
        print(f"Message ID: {message.id}")
        print(f"Date: {message.date}")
        print(f"Text: {getattr(message, 'text', '')}")
        print("-------------------")

with client:
    client.loop.run_until_complete(main())
