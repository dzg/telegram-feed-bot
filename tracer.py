import logging
import asyncio
import configparser
from telethon import TelegramClient, events

logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.INFO)

config = configparser.ConfigParser()
config.read('config.ini')

username = config['telephon']['username']
api_id = config['telephon']['api_id']
api_hash = config['telephon']['api_hash']

client = TelegramClient(username, api_id, api_hash, system_version="4.16.30-vxCUSTOM_STRING")

@client.on(events.NewMessage())
async def log_everything(event):
    """
    Log absolutely everything that comes in.
    """
    try:
        from_chat = await event.get_chat()
        chat_name = getattr(from_chat, 'title', str(event.chat_id))
        logging.info(f"RAW DETECT: Chat='{chat_name}' | ID={event.chat_id} | In={event.message.out is False} | Text='{event.message.message}'")
    except Exception as e:
        logging.error(f"Error logging raw event: {e}")

if __name__ == '__main__':
    client.start()
    logging.info("Tracer bot started...")
    client.run_until_disconnected()
