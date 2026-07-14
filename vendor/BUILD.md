# Vendored Telethon (Telegram schema LAYER 228)

`telethon-1.42.0+layer228-py3-none-any.whl` is a custom build of Telethon whose
protocol classes are generated against Telegram's **layer 228** schema.

## Why

Telegram's servers now speak schema layer 228. The newest Telethon on PyPI
(1.44.0) only speaks layer 227. When a message uses a field added in 228
(`factcheck`, `effect`, `paid_message_stars`, `rich_message`, `from_rank`, …),
Telethon 1.44 parses it with the wrong field layout, the bytes misalign, and it
then reads garbage as a non-existent "constructor id" (e.g. `1c32b11c`,
`3ae56482`). Symptoms:

- the live update loop (`getDifference`) crash-loops, dropping most posts;
- history reads (`iter_messages`, used by `/pull`) fail with `TypeNotFoundError`.

Bumping the schema to 228 fixes both. There is no layer-228 Telethon on PyPI yet,
so we build one.

## How it was built

```bash
# 1. Telethon's code generator lives only in git (the 'v1' branch)
git clone --depth 1 -b v1 https://github.com/LonamiWebs/Telethon.git repo

# 2. Swap in the current (layer 228) TL schema from Telegram Desktop
curl -sL https://raw.githubusercontent.com/telegramdesktop/tdesktop/dev/Telegram/SourceFiles/mtproto/scheme/api.tl \
    -o repo/telethon_generator/data/api.tl        # must contain: // LAYER 228

# 3. Let the hand-written Message wrapper absorb newer fields it doesn't name.
#    In telethon/tl/custom/message.py, add **kwargs to Message.__init__ and, as
#    its first body line, set them: for _k,_v in kwargs.items(): setattr(self,_k,_v)

# 4. Mark the version and build the wheel
sed -i "s/^__version__ = .*/__version__ = '1.42.0+layer228'/" repo/telethon/version.py
pip wheel --no-deps ./repo -w dist
```

## Rebuilding when Telegram bumps the layer again

Repeat the steps above (the tdesktop `api.tl` tracks the latest layer). If a new
field lands in a *hand-written* wrapper's `__init__`, the `**kwargs` shim already
absorbs it — no code change needed. Replace the wheel here and re-pin
`requirements.txt`. Once PyPI ships a layer ≥ current release, you can drop the
vendored wheel and pin that instead.
