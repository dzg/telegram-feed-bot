from telethon.extensions import html
from telethon import types

text = "Hello bold world"
entities = [types.MessageEntityBold(offset=6, length=4)]

unparsed = html.unparse(text, entities)
print("Unparsed:", unparsed)
