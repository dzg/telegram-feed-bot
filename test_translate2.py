import asyncio
from telethon import TelegramClient, functions
import configparser

config = configparser.ConfigParser()
config.read('config.ini')

username = config['telephon']['username']
api_id = config['telephon']['api_id']
api_hash = config['telephon']['api_hash']

client = TelegramClient(username + "_test", api_id, api_hash)

async def main():
    await client.start()
    try:
        channel = 'amitsegal'
        entity = await client.get_entity(channel)
        
        # Get latest message id
        msg = None
        async for m in client.iter_messages(entity, limit=1):
            msg = m
            
        print(f"Translating message {msg.id}...")
        result = await client(functions.messages.TranslateTextRequest(
            peer=entity,
            id=[msg.id],
            to_lang='en'
        ))
        
        for res in result.result:
            print("Translated:", res.text)
            
    except Exception as e:
        print(f"Error translating: {e}")

with client:
    client.loop.run_until_complete(main())
