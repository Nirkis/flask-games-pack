/* GameClient — SSE с fallback на polling.
   Использование:
     var client = GameClient('pl', {
       realtime: true,
       stateUrl: '/pl/api/state',
       actionUrl: '/pl/api/action',
       onState: function (state) { ... },
       onConnect: function () { ... },
       onDisconnect: function (reason) { ... },
     });
     client.init(); */
(function (global) {
  'use strict';

  function Client(prefix, opts) {
    opts = opts || {};
    this.prefix = prefix;
    this.stateUrl = opts.stateUrl || ('/' + prefix + '/api/state');
    this.actionUrl = opts.actionUrl || ('/' + prefix + '/api/action');
    this.eventsUrl = opts.eventsUrl || '/api/events';
    this.realtime = opts.realtime !== false;
    this.pollInterval = opts.pollInterval || 1000;
    this.sseFallbackDelay = opts.sseFallbackDelay || 3000;

    this.onState = opts.onState || function () {};
    this.onConnect = opts.onConnect || function () {};
    this.onDisconnect = opts.onDisconnect || function () {};
    this.onError = opts.onError || function (msg) { console.warn('[GameClient]', msg); };

    this._state = null;
    this._sse = null;
    this._pollTimer = null;
    this._connected = false;
    this._started = false;
  }

  Client.prototype.init = function () {
    if (this._started) return;
    this._started = true;
    var self = this;
    self._fetchOnce().then(function () {
      if (!self.realtime) { self._startPolling(); return; }
      self._startSSE();
      setTimeout(function () {
        if (!self._connected) self._startPolling();
      }, self.sseFallbackDelay);
    });
  };

  Client.prototype.action = function (name, body) {
    var self = this;
    body = body || {};
    body.action = name;
    return fetch(self.actionUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      credentials: 'same-origin',
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok || !j.ok) throw new Error(j.error || 'Ошибка действия');
        if (j.state) self._applyState(j.state);
        return j;
      });
    });
  };

  Client.prototype.leave = function () {
    this._stop();
    fetch('/api/leave', {method: 'POST', credentials: 'same-origin'})
      .catch(function () {})
      .then(function () { window.location.href = '/'; });
  };

  Client.prototype.getState = function () { return this._state; };

  Client.prototype._applyState = function (st) {
    this._state = st;
    try { this.onState(st); }
    catch (e) { console.error('[GameClient] onState:', e); }
  };

  Client.prototype._fetchOnce = function () {
    var self = this;
    return fetch(self.stateUrl + '?_=' + Date.now(), {
      cache: 'no-store', credentials: 'same-origin'
    }).then(function (r) {
      if (r.status === 404) { self._goHome(); return; }
      return r.json().then(function (j) {
        if (!j.ok) { self._goHome(); return; }
        self._applyState(j.state);
      });
    }).catch(function () {});
  };

  Client.prototype._startPolling = function () {
    var self = this;
    if (self._pollTimer) return;
    self._pollTimer = setInterval(function () { self._fetchOnce(); },
                                  self.pollInterval);
    self.onDisconnect('polling');
  };

  Client.prototype._stopPolling = function () {
    if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
  };

  Client.prototype._startSSE = function () {
    var self = this;
    try {
      self._sse = new EventSource(self.eventsUrl, {withCredentials: true});
    } catch (e) {
      self._startPolling();
      return;
    }
    self._sse.addEventListener('hello', function () {
      self._connected = true;
      self._stopPolling();
      self.onConnect();
    });
    self._sse.onopen = function () {
      self._connected = true;
      self._stopPolling();
      self.onConnect();
    };
    self._sse.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg && msg.type === 'state' && msg.state) self._applyState(msg.state);
    };
    self._sse.onerror = function () {
      self._connected = false;
      self.onDisconnect('sse-error');
      self._startPolling();
    };
  };

  Client.prototype._stop = function () {
    this._stopPolling();
    if (this._sse) { try { this._sse.close(); } catch (e) {} this._sse = null; }
    this._connected = false;
  };

  Client.prototype._goHome = function () {
    this._stop();
    fetch('/api/leave', {method: 'POST', credentials: 'same-origin'})
      .catch(function () {})
      .then(function () { window.location.href = '/'; });
  };

  global.GameClient = function (prefix, opts) {
    return new Client(prefix, opts);
  };
})(window);
