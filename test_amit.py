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
    channel = 'amitsegal'
    try:
        entity = await client.get_entity(channel)
        print(f"Channel found: {getattr(entity, 'title', 'Unknown')}")
        async for message in client.iter_messages(entity, limit=1):
            print(f"Latest message ID: {message.id}")
    except Exception as e:
        print(f"Error accessing channel: {e}")

with client:
    client.loop.run_until_complete(main())
