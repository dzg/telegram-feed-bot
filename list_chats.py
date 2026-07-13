import configparser
from telethon.sync import TelegramClient
import logging

logging.basicConfig(level=logging.INFO)

config = configparser.ConfigParser()
config.read('config.ini')

username = config['telephon']['username']
api_id = config['telephon']['api_id']
api_hash = config['telephon']['api_hash']

with TelegramClient(username, api_id, api_hash, system_version="4.16.30-vxCUSTOM_STRING") as client:
    print("Fetching dialogs...")
    for dialog in client.iter_dialogs():
        print(f"Name: {dialog.name} | ID: {dialog.id}")
