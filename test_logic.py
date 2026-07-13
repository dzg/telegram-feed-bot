SOURCE_CHATS = ['https://t.me/beholdisraelchannel', 'https://t.me/abualiexpress']
class Chat:
    def __init__(self):
        self.title = 'Amir Tsarfati'
        self.username = 'beholdisraelchannel'

from_chat = Chat()
chat_id = -1001361890342

chat_name = getattr(from_chat, 'title', str(chat_id))
if chat_id in SOURCE_CHATS or chat_name in SOURCE_CHATS:
    print("Match 1")
    
username = getattr(from_chat, 'username', None)
if username:
    formats = [username, f"@{username}", f"t.me/{username}", f"https://t.me/{username}"]
    source_strs = [s.lower() for s in SOURCE_CHATS if isinstance(s, str)]
    print(f"Formats: {formats}")
    print(f"Source strs: {source_strs}")
    match = any(f.lower() in source_strs for f in formats)
    print(f"Match 2: {match}")
