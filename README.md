# Flask-Games-pack

Мультиплеерный игровой портал с автообнаружением игр. Игры — самодостаточные
пакеты: код, данные, шаблоны и статика живут рядом.

## Запуск

    pip install -r requirements.txt
    PORTAL_DEV=1 python app.py

Опционально, чтобы показать примеры игр в списке:

    PORTAL_EXAMPLES=1 PORTAL_DEV=1 python app.py

## Структура

    games/<game>/
    ├── __init__.py            ← код игры (GAME_META, bp, хуки)
    ├── data/                  ← JSON-данные игры
    │   └── *.json
    └── templates/<game>/      ← шаблоны этой игры
        └── index.html

## Как добавить игру

1. Скопируй `games/_example_step/` в `games/my_game/`.
2. Поменяй `GAME_META`: `code`, `prefix`, `name`, `template`.
3. Настрой данные в `games/my_game/data/*.json`.
4. Перепиши логику игры.
5. Перезапусти сервер — игра появится в списке автоматически.

Ничего в `app.py`, `games/registry.py` или `templates/index.html` править
не нужно.

## Контракт модуля

Обязательные атрибуты:

- `GAME_META` — словарь (см. ниже)
- `bp` — Flask Blueprint

Обязательные функции (устанавливаются через `GameServer.install`):

- `create_game(name, **opts) -> (code, token, err)`
- `join_game(code, name, **opts) -> (token, err)`
- `has_player(code, token) -> bool`
- `leave(code, token) -> None`

Опциональные (тоже из GameServer):

- `sweep(now)` — вызывается порталом раз в 10 сек
- `list_active()` — для панели активных игр
- `is_joinable(code)` — для `/api/rejoin`
- `build_state_for_token(code, token)` — для SSE

## GAME_META

    {
      'code': 'hl',                    # 2 буквы, ключ в session
      'prefix': 'HL',                  # 2 заглавные, префикс кода игры
      'name': 'HearthLite',            # отображается на лендинге
      'players': '2-3',                # подпись на лендинге
      'template': 'hearthlite/index.html',
      'modes': [2, 3],                 # опционально: селектор числа игроков
      'realtime': True,                # опционально: SSE вместо polling
      'order': 10,                     # порядок в списке
    }

Префикс не должен совпадать с уже занятыми. Проверить:
`GET /api/games`.

## SSE (для realtime)

Каждая realtime-игра должна:

1. В `_tick_one` после каждого тика вызвать `_broadcast(g)`.
2. В `api_action` после успешного действия вызвать `_broadcast(g)`.

`_broadcast` собирает состояние для всех подключённых игроков и кладёт
в шину `events_bus.push_many`. Портал сам раздаёт через `/api/events`.

Шаблон подключает `GameClient` с `realtime: true`.

## Схема данных

    from games.data_loader import load

    CARDS = load('cards', schema={
        '*': {
            'name': str,
            'type': ['minion', 'spell'],
            'cost': int,
        }
    })

Опечатка в JSON валит старт сервера с человеческой ошибкой, а не крашем
через час в бою.

## Продакшен

    export PORTAL_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
    gunicorn -k gthread --threads 8 -w 1 app:app

### Важно

- `-k gthread` обязателен. Sync-воркеры блокируются на SSE.
- `-w 1` — GAMES и подписки живут в памяти процесса.
- Nginx: `proxy_buffering off; proxy_read_timeout 3600s; proxy_set_header Connection '';`

## Python

Целевая версия — 3.6.8 (EOL). Апгрейд до 3.10+ рекомендован до продакшена.
