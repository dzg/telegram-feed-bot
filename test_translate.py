import asyncio
from telethon import TelegramClient
from telethon.tl.functions.messages import TranslateTextRequest
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
        # Hebrew text to translate
        text = "שלום, מה שלומך?"
        result = await client(TranslateTextRequest(
            peer='amitsegal',
            id=[], # or we can provide text
            text=[text],
            to_lang='en'
        ))
        print("Translation result:", result.result[0].text)
    except Exception as e:
        print(f"Error translating: {e}")

with client:
    client.loop.run_until_complete(main())
