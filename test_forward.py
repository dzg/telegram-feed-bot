import asyncio
import configparser
from telethon import TelegramClient

config = configparser.ConfigParser()
config.read('config.ini')

username = config['telephon']['username']
api_id = config['telephon']['api_id']
api_hash = config['telephon']['api_hash']

TARGET_CHANNEL = config.get('settings', 'target_channel', fallback='https://t.me/newzzzzzil')

client = TelegramClient("your_info_test", api_id, api_hash)

async def main():
    await client.start()
    channel = 'beholdisraelchannel'
    entity = await client.get_entity(channel)
    
    async for message in client.iter_messages(entity, limit=1):
        print(f"Forwarding message {message.id} to {TARGET_CHANNEL}")
        try:
            await client.forward_messages(TARGET_CHANNEL, message)
            print("Successfully forwarded!")
        except Exception as e:
            print(f"Error forwarding: {e}")

with client:
    client.loop.run_until_complete(main())
