# -*- coding: utf-8 -*-
"""Шина SSE-событий. Каждый подписчик — deque на 256 сообщений."""
import json
import threading
from collections import deque


class Subscriber(object):
    __slots__ = ('code', 'token', 'cv', 'queue', 'closed')

    def __init__(self, code, token):
        self.code = code
        self.token = token
        self.cv = threading.Condition()
        self.queue = deque(maxlen=256)
        self.closed = False

    def push(self, data):
        with self.cv:
            if self.closed:
                return
            self.queue.append(data)
            self.cv.notify()

    def wait_next(self, timeout):
        with self.cv:
            if not self.queue and not self.closed:
                self.cv.wait(timeout)
            if self.closed and not self.queue:
                return None, 'closed'
            if not self.queue:
                return None, None
            return self.queue.popleft(), None

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify_all()


_LOCK = threading.Lock()
_SUBS = {}


def subscribe(code, token):
    sub = Subscriber(code, token)
    with _LOCK:
        _SUBS.setdefault(code, {}).setdefault(token, []).append(sub)
    return sub


def unsubscribe(sub):
    with _LOCK:
        by_tok = _SUBS.get(sub.code)
        if by_tok:
            lst = by_tok.get(sub.token)
            if lst:
                try:
                    lst.remove(sub)
                except ValueError:
                    pass
                if not lst:
                    by_tok.pop(sub.token, None)
            if not by_tok:
                _SUBS.pop(sub.code, None)
    sub.close()


def push_many(code, items):
    """items = [(token, data), ...]."""
    with _LOCK:
        by_tok = dict(_SUBS.get(code, {}))
    for token, data in items:
        for s in list(by_tok.get(token, [])):
            s.push(data)


def drop_code(code):
    with _LOCK:
        by_tok = _SUBS.pop(code, {})
    for lst in by_tok.values():
        for s in lst:
            s.close()


def drop_player(code, token):
    """Закрыть SSE одного игрока, не трогая остальных."""
    with _LOCK:
        by_tok = _SUBS.get(code)
        if not by_tok:
            return
        lst = by_tok.pop(token, [])
        if not by_tok:
            _SUBS.pop(code, None)
    for s in lst:
        s.close()


def sweep_dead():
    """Закрывает утекшие подписки. Вызывается из sweeper-потока портала."""
    with _LOCK:
        for code, by_tok in list(_SUBS.items()):
            for token, lst in list(by_tok.items()):
                for s in list(lst):
                    if s.closed:
                        try:
                            lst.remove(s)
                        except ValueError:
                            pass
                if not lst:
                    by_tok.pop(token, None)
            if not by_tok:
                _SUBS.pop(code, None)


def to_sse(data):
    # ensure_ascii=True — экранирует \u2028/\u2029, которые ломают часть прокси
    return 'data: %s\n\n' % json.dumps(data, ensure_ascii=True)
