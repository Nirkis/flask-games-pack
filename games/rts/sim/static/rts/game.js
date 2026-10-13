/* Наследие Азерота — визуальный клиент. Canvas 2D, ES5. */
(function () {
'use strict';

var TILE = 32;
var ZOOM_MIN = 0.5;
var ZOOM_MAX = 2.0;
var ZOOM_STEP = 1.1;
var CAM_SPEED = 15;
var DRAG_THRESHOLD = 4;
var DOUBLE_CLICK_MS = 350;
var LS_CAM = 'rts.cam.v1';

var COLORS = {
    me:              '#5bd45b',
    enemy:           '#e05252',
    neutral:         '#888888',
    meBuilding:      '#3a8ed4',
    enemyBuilding:   '#a03535',
    neutralBuilding: '#666666',
    mineGold:        '#e8c33a',
    mineWood:        '#8a5a2a',
    projectile:      '#ffe066'
};
var TILE_COLORS = {
    0: '#4a7a3a', 1: '#2a5a9a', 2: '#2a4a1a',
    3: '#6a6a6a', 4: '#3a3a3a'
};

// ---------- Глобалы ----------

var STATE = null;
var client = null;
var canvas = null, ctx = null;
var mmCanvas = null, mmCtx = null;
var portrait = null, pctx = null;
var root = null;
var els = {};

var cam = {x: 0, y: 0, zoom: 1.0};
var keys = {};
var panning = null;
var drag = null;
var selection = {};
var hotkeys = {};              // '1'..'9' -> [ids]
var mode = null;               // null | 'ability-point' | 'ability-enemy'
                               // | 'ability-resource' | 'ability-own-building'
                               // | 'build-ability' | 'patrol'
var pendingAbility = null;
var pendingOption = null;
var patrolFirst = null;
var currentMenu = null;        // id открытого подменю
var lastClickId = null;
var lastClickT = 0;
var hintT = null;

var abilityHotkeyMap = {};     // lowercase -> {id, ab}
var _lastCmdsJson = '';        // дедупликация DOM-перерисовки сетки
var _lastInfoHtml = '';

// ---------- Утилиты ----------

function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

function isBuilding(e) {
    return !!(e && STATE && STATE.buildings_data
              && STATE.buildings_data[e.type_id] !== undefined);
}
function isWorker(e) {
    if (!e) return false;
    var t = e.type_id;
    return t === 'worker' || t === 'peon' || t === 'acolyte' || t === 'wisp';
}
function mySlot() {
    return (STATE && typeof STATE.slot === 'number') ? STATE.slot : 0;
}
function ownerColor(e) {
    if (!STATE) return COLORS.neutral;
    if (e.owner === mySlot()) return COLORS.me;
    if (e.owner === -1) return COLORS.neutral;
    return COLORS.enemy;
}
function ownerBuildingColor(e) {
    if (!STATE) return COLORS.neutralBuilding;
    if (e.owner === mySlot()) return COLORS.meBuilding;
    if (e.owner === -1) return COLORS.neutralBuilding;
    return COLORS.enemyBuilding;
}
function mapSize() {
    if (!STATE || !STATE.map || !STATE.map.size) return [64, 64];
    return [STATE.map.size[0], STATE.map.size[1]];
}
function getTiles() {
    if (!STATE || !STATE.map) return null;
    var t = STATE.map.tiles;
    if (!t) return null;
    if (t.length && Array.isArray(t[0])) {
        var flat = [];
        for (var i = 0; i < t.length; i++) flat = flat.concat(t[i]);
        return flat;
    }
    return t;
}
function toast(msg) {
    if (!els.toast) return;
    els.toast.textContent = String(msg);
    els.toast.style.display = 'block';
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { els.toast.style.display = 'none'; }, 2500);
}
function setHint(msg) {
    if (!els.hint) return;
    els.hint.textContent = msg || '';
    if (hintT) clearTimeout(hintT);
    if (msg) hintT = setTimeout(function () { els.hint.textContent = ''; }, 3500);
}

// ---------- Камера ----------

function loadCam() {
    try {
        var s = localStorage.getItem(LS_CAM);
        if (!s) return;
        var c = JSON.parse(s);
        if (c && isFinite(c.x) && isFinite(c.y) && isFinite(c.zoom)) {
            cam.x = c.x; cam.y = c.y;
            cam.zoom = clamp(c.zoom, ZOOM_MIN, ZOOM_MAX);
        }
    } catch (e) {}
}
function saveCam() {
    try { localStorage.setItem(LS_CAM, JSON.stringify(cam)); } catch (e) {}
}
function clampCam() {
    var ms = mapSize();
    var mw = ms[0], mh = ms[1];
    var ts = TILE * cam.zoom;
    var vw = canvas.width / ts;
    var vh = canvas.height / ts;
    if (!isFinite(cam.x)) cam.x = 0;
    if (!isFinite(cam.y)) cam.y = 0;
    if (vw >= mw) cam.x = (mw - vw) / 2;
    else cam.x = clamp(cam.x, 0, mw - vw);
    if (vh >= mh) cam.y = (mh - vh) / 2;
    else cam.y = clamp(cam.y, 0, mh - vh);
}
function w2s(wx, wy) {
    return [(wx - cam.x) * TILE * cam.zoom,
            (wy - cam.y) * TILE * cam.zoom];
}
function s2w(sx, sy) {
    var s = TILE * cam.zoom;
    return [sx / s + cam.x, sy / s + cam.y];
}
function centerOn(wx, wy) {
    cam.x = wx - (canvas.width  / (TILE * cam.zoom)) / 2;
    cam.y = wy - (canvas.height / (TILE * cam.zoom)) / 2;
    clampCam(); saveCam();
}

function sizeCanvas() {
    if (!canvas || !root) return;
    var rect = root.getBoundingClientRect();
    var w = rect.width, h = rect.height;
    if (!w || w < 32) w = window.innerWidth;
    if (!h || h < 32) h = window.innerHeight - 172;
    canvas.width  = Math.max(1, Math.floor(w));
    canvas.height = Math.max(1, Math.floor(h - 172));
    if (mmCanvas) {
        var mr = mmCanvas.getBoundingClientRect();
        mmCanvas.width  = Math.max(1, Math.floor(mr.width  || 200));
        mmCanvas.height = Math.max(1, Math.floor(mr.height || 200));
    }
}

// ---------- Выделение ----------

function selArray() {
    if (!STATE || !STATE.entities) return [];
    var out = [];
    for (var i = 0; i < STATE.entities.length; i++) {
        if (selection[STATE.entities[i].id]) out.push(STATE.entities[i]);
    }
    return out;
}
function selIds() {
    var out = [];
    for (var k in selection) out.push(+k);
    return out;
}
function findEntity(id) {
    var list = (STATE && STATE.entities) || [];
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
}
function selectSameType(typeId, additive) {
    if (!additive) selection = {};
    var list = STATE.entities || [];
    for (var i = 0; i < list.length; i++) {
        var u = list[i];
        if (u.owner === mySlot() && u.type_id === typeId) {
            selection[u.id] = true;
        }
    }
    updatePanels();
}
function pickEntity(wx, wy) {
    if (!STATE) return null;
    var list = STATE.entities || [];
    var best = null, bestD2 = Infinity;
    for (var i = 0; i < list.length; i++) {
        var e = list[i];
        if (!e || !e.pos) continue;
        var r = isBuilding(e) ? 1.4 : 0.55;
        var dx = e.pos[0] - wx, dy = e.pos[1] - wy;
        var d2 = dx * dx + dy * dy;
        if (d2 < r * r && d2 < bestD2) { best = e; bestD2 = d2; }
    }
    return best;
}
function pickNode(wx, wy) {
    var nodes = (STATE && STATE.resource_nodes) || [];
    for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        if (!n || !n.pos) continue;
        var dx = n.pos[0] - wx, dy = n.pos[1] - wy;
        if (dx * dx + dy * dy < 1.4) return n;
    }
    return null;
}

// ---------- Рендер ----------

function draw() {
    if (!ctx) return;
    var W = canvas.width, H = canvas.height;
    ctx.fillStyle = '#101510';
    ctx.fillRect(0, 0, W, H);
    if (!STATE) { renderMinimap(); return; }
    drawTiles(W, H);
    drawNodes();
    drawEntities();
    drawProjectiles();
    drawDragBox();
    renderMinimap();
}

function drawTiles(W, H) {
    var tiles = getTiles();
    if (!tiles) return;
    var ms = mapSize();
    var mw = ms[0], mh = ms[1];
    var ts = TILE * cam.zoom;
    var tx0 = Math.max(0, Math.floor(cam.x));
    var ty0 = Math.max(0, Math.floor(cam.y));
    var tx1 = Math.min(mw - 1, Math.ceil(cam.x + W / ts));
    var ty1 = Math.min(mh - 1, Math.ceil(cam.y + H / ts));
    var sz = Math.ceil(ts) + 1;
    for (var ty = ty0; ty <= ty1; ty++) {
        var sy = ((ty - cam.y) * ts) | 0;
        var row = ty * mw;
        for (var tx = tx0; tx <= tx1; tx++) {
            ctx.fillStyle = TILE_COLORS[tiles[row + tx]] || '#000';
            ctx.fillRect(((tx - cam.x) * ts) | 0, sy, sz, sz);
        }
    }
}

function drawNodes() {
    var nodes = STATE.resource_nodes || [];
    for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        if (!n || !n.pos) continue;
        var p = w2s(n.pos[0], n.pos[1]);
        var s = 24 * cam.zoom;
        var m = 60;
        if (p[0] < -m || p[1] < -m ||
            p[0] > canvas.width + m || p[1] > canvas.height + m) continue;
        var isGold = (n.type === 'gold_mine' || n.type === 'gold');
        ctx.fillStyle = isGold ? COLORS.mineGold : COLORS.mineWood;
        ctx.fillRect(p[0] - s/2, p[1] - s/2, s, s);
        ctx.strokeStyle = 'rgba(0,0,0,0.75)';
        ctx.lineWidth = 2;
        ctx.strokeRect(p[0] - s/2, p[1] - s/2, s, s);
        ctx.fillStyle = '#000';
        ctx.font = 'bold ' + Math.round(12 * cam.zoom) + 'px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(isGold ? 'G' : 'W', p[0], p[1]);
    }
}

function drawEntities() {
    var list = STATE.entities || [];
    var i, e;
    for (i = 0; i < list.length; i++) {
        e = list[i]; if (isBuilding(e)) drawEntity(e, true);
    }
    for (i = 0; i < list.length; i++) {
        e = list[i]; if (!isBuilding(e)) drawEntity(e, false);
    }
}

function drawEntity(e, isB) {
    if (!e || !e.pos) return;
    var p = w2s(e.pos[0], e.pos[1]);
    var z = cam.zoom;
    var size = isB ? 44 * z : 22 * z;
    var half = size / 2;
    var m = 80;
    if (p[0] < -m || p[1] < -m ||
        p[0] > canvas.width + m || p[1] > canvas.height + m) return;

    if (selection[e.id]) {
        ctx.strokeStyle = '#5bd45b';
        ctx.lineWidth = 2;
        ctx.beginPath();
        if (isB) {
            ctx.strokeRect(p[0] - half - 3, p[1] - half - 3, size + 6, size + 6);
        } else {
            ctx.arc(p[0], p[1], half + 3, 0, Math.PI * 2);
            ctx.stroke();
        }
    }

    if (isB) {
        ctx.fillStyle = ownerBuildingColor(e);
        ctx.fillRect(p[0] - half, p[1] - half, size, size);
        ctx.strokeStyle = 'rgba(0,0,0,0.75)';
        ctx.lineWidth = 2;
        ctx.strokeRect(p[0] - half, p[1] - half, size, size);
    } else {
        ctx.fillStyle = ownerColor(e);
        ctx.beginPath();
        ctx.arc(p[0], p[1], half, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = 'rgba(0,0,0,0.75)';
        ctx.lineWidth = 1.5;
        ctx.stroke();
    }

    var letter = String(e.type_id || '?').slice(0, 1).toUpperCase();
    ctx.fillStyle = '#000';
    ctx.font = 'bold ' + Math.round((isB ? 16 : 12) * z) + 'px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(letter, p[0], p[1]);

    if (e.hp != null && e.max_hp) {
        var show = isB || e.hp < e.max_hp || selection[e.id];
        if (show) {
            var bw = size * 0.95;
            var bh = Math.max(3, 3 * z);
            var bx = p[0] - bw / 2;
            var by = p[1] - half - bh - 3;
            ctx.fillStyle = 'rgba(0,0,0,0.75)';
            ctx.fillRect(bx, by, bw, bh);
            var r = clamp(e.hp / e.max_hp, 0, 1);
            ctx.fillStyle = r > 0.5 ? '#5bd45b'
                           : (r > 0.25 ? '#e8c33a' : '#e05252');
            ctx.fillRect(bx, by, bw * r, bh);
        }
    }
}

function drawProjectiles() {
    var list = STATE.projectiles || [];
    for (var i = 0; i < list.length; i++) {
        var pr = list[i];
        if (!pr || !pr.pos) continue;
        var p = w2s(pr.pos[0], pr.pos[1]);
        ctx.fillStyle = COLORS.projectile;
        ctx.beginPath();
        ctx.arc(p[0], p[1], 2.5 * cam.zoom, 0, Math.PI * 2);
        ctx.fill();
    }
}

function drawDragBox() {
    if (!drag || !drag.active) return;
    var x = Math.min(drag.x0, drag.x1);
    var y = Math.min(drag.y0, drag.y1);
    var w = Math.abs(drag.x1 - drag.x0);
    var h = Math.abs(drag.y1 - drag.y0);
    ctx.fillStyle = 'rgba(91,212,91,0.10)';
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = '#5bd45b';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x + 0.5, y + 0.5, w, h);
}

function renderMinimap() {
    if (!mmCtx) return;
    var W = mmCanvas.width, H = mmCanvas.height;
    mmCtx.fillStyle = '#0a0a0a';
    mmCtx.fillRect(0, 0, W, H);
    if (!STATE || !STATE.map) return;
    var tiles = getTiles();
    if (!tiles) return;
    var ms = mapSize();
    var mw = ms[0], mh = ms[1];
    var sc = Math.min(W / mw, H / mh);

    for (var y = 0; y < mh; y++) {
        for (var x = 0; x < mw; x++) {
            mmCtx.fillStyle = TILE_COLORS[tiles[y * mw + x]] || '#000';
            mmCtx.fillRect(x * sc, y * sc, Math.ceil(sc), Math.ceil(sc));
        }
    }
    var nodes = STATE.resource_nodes || [];
    for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        if (!n || !n.pos) continue;
        mmCtx.fillStyle = (n.type === 'gold_mine' || n.type === 'gold')
            ? '#f5c542' : '#8a5a2a';
        mmCtx.fillRect(n.pos[0] * sc - 1, n.pos[1] * sc - 1, 2.5, 2.5);
    }
    var list = STATE.entities || [];
    for (var j = 0; j < list.length; j++) {
        var e = list[j];
        if (!e || !e.pos) continue;
        var mine = e.owner === mySlot();
        var isB = isBuilding(e);
        mmCtx.fillStyle = mine
            ? (isB ? '#5bd4ff' : '#5bd45b')
            : (isB ? '#ff5bd4' : '#e05252');
        var px = e.pos[0] * sc, py = e.pos[1] * sc;
        if (isB) mmCtx.fillRect(px - 2, py - 2, 4, 4);
        else {
            mmCtx.beginPath();
            mmCtx.arc(px, py, 1.6, 0, Math.PI * 2);
            mmCtx.fill();
        }
    }
    var vx = cam.x * sc, vy = cam.y * sc;
    var vw = (canvas.width  / (TILE * cam.zoom)) * sc;
    var vh = (canvas.height / (TILE * cam.zoom)) * sc;
    mmCtx.strokeStyle = '#fff';
    mmCtx.lineWidth = 1;
    mmCtx.strokeRect(vx + 0.5, vy + 0.5, vw, vh);
}

// ---------- Мышь ----------

function localPos(e) {
    var r = canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
}

function onMouseDown(e) {
    var p = localPos(e);
    if (e.button === 1) {
        panning = {x: p[0], y: p[1], camX: cam.x, camY: cam.y};
        e.preventDefault();
        return;
    }
    if (e.button === 2) { onRightDown(e); return; }
    if (e.button === 0) {
        if (mode) { handleModeClick(p[0], p[1]); return; }
        var w = s2w(p[0], p[1]);
        var hit = pickEntity(w[0], w[1]);
        drag = {
            active: false,
            x0: p[0], y0: p[1], x1: p[0], y1: p[1],
            shift: e.shiftKey,
            clickedId: hit ? hit.id : null
        };
    }
}

function onMouseMove(e) {
    var p = localPos(e);
    if (panning) {
        var s = TILE * cam.zoom;
        cam.x = panning.camX - (p[0] - panning.x) / s;
        cam.y = panning.camY - (p[1] - panning.y) / s;
        clampCam(); saveCam();
        return;
    }
    if (drag) {
        if (!drag.active &&
            (Math.abs(p[0] - drag.x0) > DRAG_THRESHOLD ||
             Math.abs(p[1] - drag.y0) > DRAG_THRESHOLD)) {
            drag.active = true;
        }
        drag.x1 = p[0]; drag.y1 = p[1];
    }
}

function onMouseUp(e) {
    if (e.button === 1) { panning = null; return; }
    if (e.button === 0 && drag) finishLeftClick();
}

function finishLeftClick() {
    var d = drag; drag = null;
    var now = performance.now();
    if (!d) return;

    if (!d.active) {
        var hit = d.clickedId != null ? findEntity(d.clickedId) : null;

        if (hit && hit.owner === mySlot() &&
            lastClickId === hit.id && now - lastClickT < DOUBLE_CLICK_MS) {
            selectSameType(hit.type_id, d.shift);
            lastClickId = null;
            return;
        }

        if (hit) {
            if (!d.shift) selection = {};
            if (hit.owner === mySlot()) selection[hit.id] = true;
        } else if (!d.shift) {
            selection = {};
        }
        lastClickId = hit ? hit.id : null;
        lastClickT = now;
        updatePanels();
        return;
    }

    var a = s2w(Math.min(d.x0, d.x1), Math.min(d.y0, d.y1));
    var b = s2w(Math.max(d.x0, d.x1), Math.max(d.y0, d.y1));
    if (!d.shift) selection = {};
    var list = STATE.entities || [];
    for (var i = 0; i < list.length; i++) {
        var u = list[i];
        if (u.owner !== mySlot()) continue;
        if (isBuilding(u)) continue;
        if (u.pos[0] >= a[0] && u.pos[0] <= b[0] &&
            u.pos[1] >= a[1] && u.pos[1] <= b[1]) {
            selection[u.id] = true;
        }
    }
    updatePanels();
}

function onContextMenu(e) { e.preventDefault(); }

function onRightDown(e) {
    e.preventDefault();
    if (!STATE) return;
    var p = localPos(e);
    var w = s2w(p[0], p[1]);
    var ids = selIds();
    if (!ids.length) return;

    var node = pickNode(w[0], w[1]);
    if (node) {
        var workers = selArray().filter(isWorker);
        if (workers.length) {
            var wids = workers.map(function (u) { return u.id; });
            sendUseAbility('gather', {ids: wids, node_id: node.id});
            return;
        }
    }

    var target = pickEntity(w[0], w[1]);
    if (target && target.owner !== mySlot() && target.owner !== -1) {
        sendUseAbility('attack', {ids: ids, target_id: target.id});
        return;
    }
    if (target && target.owner === mySlot() && isBuilding(target)) {
        var ws = selArray().filter(isWorker);
        if (ws.length) {
            sendUseAbility('repair', {ids: ids, target_id: target.id});
            return;
        }
    }
    sendUseAbility('move', {ids: ids, pos: [w[0], w[1]]});
}

function handleModeClick(sx, sy) {
    var w = s2w(sx, sy);

    if (mode === 'build-ability' && pendingOption) {
        sendUseAbility(pendingAbility, {
            ids: selIds(), option: pendingOption,
            pos: [Math.round(w[0]), Math.round(w[1])]
        });
        cancelMode();
        return;
    }
    if (mode === 'ability-point' && pendingAbility) {
        sendUseAbility(pendingAbility, {ids: selIds(), pos: [w[0], w[1]]});
        cancelMode();
        return;
    }
    if (mode === 'ability-enemy' && pendingAbility) {
        var tgt = pickEntity(w[0], w[1]);
        if (tgt && tgt.owner !== mySlot() && tgt.owner !== -1) {
            sendUseAbility(pendingAbility, {ids: selIds(), target_id: tgt.id});
        }
        cancelMode();
        return;
    }
    if (mode === 'ability-resource' && pendingAbility) {
        var node = pickNode(w[0], w[1]);
        if (node) {
            sendUseAbility(pendingAbility, {ids: selIds(), node_id: node.id});
        }
        cancelMode();
        return;
    }
    if (mode === 'ability-own-building' && pendingAbility) {
        var b = pickEntity(w[0], w[1]);
        if (b && b.owner === mySlot() && isBuilding(b)) {
            sendUseAbility(pendingAbility, {ids: selIds(), target_id: b.id});
        }
        cancelMode();
        return;
    }
    if (mode === 'patrol') {
        if (!patrolFirst) {
            patrolFirst = [w[0], w[1]];
            setHint('Патруль: точка 2');
        } else {
            sendUseAbility('patrol', {ids: selIds(), p1: patrolFirst, p2: [w[0], w[1]]});
            cancelMode();
        }
        return;
    }
}

function cancelMode() {
    mode = null; pendingAbility = null; pendingOption = null;
    patrolFirst = null; setHint('');
}

function onWheel(e) {
    e.preventDefault();
    var p = localPos(e);
    var before = s2w(p[0], p[1]);
    var factor = e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
    cam.zoom = clamp(cam.zoom * factor, ZOOM_MIN, ZOOM_MAX);
    var after = s2w(p[0], p[1]);
    cam.x += before[0] - after[0];
    cam.y += before[1] - after[1];
    clampCam(); saveCam();
}

function onMinimapDown(e) {
    if (e.button !== 0 || !STATE || !STATE.map) return;
    var r = mmCanvas.getBoundingClientRect();
    var mx = e.clientX - r.left, my = e.clientY - r.top;
    var ms = mapSize();
    var sc = Math.min(mmCanvas.width / ms[0], mmCanvas.height / ms[1]);
    centerOn(mx / sc, my / sc);
}

// ---------- Клавиатура ----------

function onKeyDown(e) {
    var k = e.key || '';
    var lower = k.toLowerCase();
    keys[lower] = true;

    // 1. Hotkey-группы: Ctrl+1..9 сохранить, 1..9 выбрать
    if (lower >= '1' && lower <= '9') {
        if (e.ctrlKey || e.metaKey) {
            hotkeys[lower] = selIds();
            e.preventDefault();
            return;
        }
        var ids = hotkeys[lower] || [];
        selection = {};
        currentMenu = null;
        for (var i = 0; i < ids.length; i++) selection[ids[i]] = true;
        updatePanels();
        return;
    }

    // 2. Escape — сброс режима / закрыть меню
    if (k === 'Escape') {
        if (currentMenu) { currentMenu = null; updatePanels(); }
        else cancelMode();
        return;
    }

    // 3. Space — центрирование
    if (k === ' ') {
        e.preventDefault();
        focusHero();
        return;
    }

    // 4. F1..F4 — группы 1..4
    if (k === 'F1' || k === 'F2' || k === 'F3' || k === 'F4') {
        e.preventDefault();
        var idx = k.charAt(1);
        var hids = hotkeys[idx] || [];
        selection = {};
        currentMenu = null;
        for (var j = 0; j < hids.length; j++) selection[hids[j]] = true;
        focusHero();
        updatePanels();
        return;
    }

    // 5. Способности выделенного существа (по hotkey из abilities.json)
    if (abilityHotkeyMap[lower]) {
        e.preventDefault();
        var entry = abilityHotkeyMap[lower];
        if (entry.menuOption) {
            var subj = selArray()[0];
            if (subj) chooseOption(subj, entry.menuOption, entry.option);
        } else {
            triggerAbility(entry.id, entry.ab);
        }
        return;
    }
}

function onKeyUp(e) { keys[(e.key || '').toLowerCase()] = false; }

function updateCameraFromKeys(dt) {
    var speed = CAM_SPEED * dt / cam.zoom;
    var dx = 0, dy = 0;
    if (keys['arrowleft'])  dx -= speed;
    if (keys['arrowright']) dx += speed;
    if (keys['arrowup'])    dy -= speed;
    if (keys['arrowdown'])  dy += speed;
    // WASD — только если нет выделения (иначе конфликт с abilities W/A/S/D)
    if (!selIds().length) {
        if (keys['w']) dy -= speed;
        if (keys['s']) dy += speed;
        if (keys['a']) dx -= speed;
        if (keys['d']) dx += speed;
    }
    if (dx || dy) {
        cam.x += dx; cam.y += dy;
        clampCam(); saveCam();
    }
}

function focusHero() {
    var sel = selArray();
    if (sel.length) {
        var sx = 0, sy = 0;
        for (var i = 0; i < sel.length; i++) { sx += sel[i].pos[0]; sy += sel[i].pos[1]; }
        centerOn(sx / sel.length, sy / sel.length);
        return;
    }
    var list = (STATE && STATE.entities) || [];
    for (var j = 0; j < list.length; j++) {
        var e = list[j];
        if (e.owner === mySlot() && isBuilding(e)) {
            centerOn(e.pos[0], e.pos[1]);
            return;
        }
    }
}

// ---------- Отправка ----------

function send(action, params) {
    if (!client || typeof client.action !== 'function') {
        toast('Нет связи с сервером');
        return;
    }
    try {
        var p = client.action(action, params || {});
        if (p && typeof p.catch === 'function') {
            p.catch(function (e) { toast((e && e.message) || 'Ошибка'); });
        }
    } catch (e) {
        toast((e && e.message) || 'Ошибка');
    }
}

function sendUseAbility(abilityId, extra) {
    var params = {ability: abilityId};
    if (extra) for (var k in extra) params[k] = extra[k];
    send('use_ability', params);
}

// ---------- Панели ----------

function readRes() {
    if (!STATE || !STATE.resources) {
        return {gold: 0, wood: 0, food_used: 0, food_cap: 0};
    }
    var key = String(mySlot());
    return STATE.resources[key]
        || STATE.resources[mySlot()]
        || {gold:0, wood:0, food_used:0, food_cap:0};
}

function updatePanels() {
    if (!STATE || !els.gold) return;

    var r = readRes();
    els.gold.textContent = 'Gold: ' + (r.gold | 0);
    els.wood.textContent = 'Wood: ' + (r.wood | 0);
    els.food.textContent = 'Food: ' + (r.food_used | 0) + '/' + (r.food_cap | 0);
    els.selCount.textContent = 'Выделено: ' + selArray().length;

    var sel = selArray();
    if (sel.length === 1) {
        var e = sel[0];
        var data = (isBuilding(e) ? STATE.buildings_data : STATE.units_data) || {};
        var ud = data[e.type_id] || {};
        els.portraitName.textContent = ud.name || e.type_id;
        drawPortraitSingle(e);
        renderInfoSingle(e, ud);
    } else if (sel.length > 1) {
        els.portraitName.textContent = sel.length + ' юнитов';
        drawPortraitMulti(sel);
        renderInfoMulti(sel);
    } else {
        els.portraitName.textContent = '—';
        if (pctx) {
            pctx.fillStyle = '#0a0a0a';
            pctx.fillRect(0, 0, 96, 96);
        }
        if (els.info) els.info.innerHTML = '';
    }

    rebuildAbilityHotkeyMap(sel);
    renderCommands(sel);
}

function rebuildAbilityHotkeyMap(sel) {
    abilityHotkeyMap = {};
    if (!sel.length) return;
    var abilitiesData = STATE.abilities_data || {};

    if (currentMenu) {
        var menuAb = abilitiesData[currentMenu];
        if (menuAb && menuAb.options) {
            for (var i = 0; i < menuAb.options.length; i++) {
                var o = menuAb.options[i];
                if (!o.hotkey) continue;
                var mk = String(o.hotkey).toLowerCase();
                if (!abilityHotkeyMap[mk]) {
                    abilityHotkeyMap[mk] = {menuOption: currentMenu, option: o};
                }
            }
        }
        return;
    }

    var subj = sel[0];
    var data = (isBuilding(subj) ? STATE.buildings_data : STATE.units_data) || {};
    var ud = data[subj.type_id] || {};
    var abilities = ud.abilities || [];
    for (var j = 0; j < abilities.length; j++) {
        var id = abilities[j];
        var ab = abilitiesData[id];
        if (!ab || !ab.hotkey) continue;
        var k = String(ab.hotkey).toLowerCase();
        if (!abilityHotkeyMap[k]) {
            abilityHotkeyMap[k] = {id: id, ab: ab};
        }
    }
}

function drawPortraitSingle(e) {
    if (!pctx) return;
    pctx.fillStyle = '#0a0a0a';
    pctx.fillRect(0, 0, 96, 96);
    var isB = isBuilding(e);
    if (isB) {
        pctx.fillStyle = ownerBuildingColor(e);
        pctx.fillRect(16, 16, 64, 64);
    } else {
        pctx.fillStyle = ownerColor(e);
        pctx.beginPath();
        pctx.arc(48, 48, 30, 0, Math.PI * 2);
        pctx.fill();
    }
    pctx.fillStyle = '#000';
    pctx.font = 'bold 36px sans-serif';
    pctx.textAlign = 'center';
    pctx.textBaseline = 'middle';
    pctx.fillText(String(e.type_id || '?').slice(0, 1).toUpperCase(), 48, 48);
}
function drawPortraitMulti(sel) {
    if (!pctx) return;
    pctx.fillStyle = '#0a0a0a';
    pctx.fillRect(0, 0, 96, 96);
    var n = Math.min(4, sel.length);
    for (var i = 0; i < n; i++) {
        var e = sel[i];
        var x = (i % 2) * 46 + 2;
        var y = Math.floor(i / 2) * 46 + 2;
        pctx.fillStyle = isBuilding(e) ? ownerBuildingColor(e) : ownerColor(e);
        pctx.fillRect(x, y, 44, 44);
        pctx.fillStyle = '#000';
        pctx.font = 'bold 20px sans-serif';
        pctx.textAlign = 'center';
        pctx.textBaseline = 'middle';
        pctx.fillText(String(e.type_id || '?').slice(0, 1).toUpperCase(),
                      x + 22, y + 22);
    }
}

function renderInfoSingle(e, ud) {
    var out = [];
    out.push('<b>' + (ud.name || e.type_id) + '</b>');
    out.push('HP: ' + e.hp + ' / ' + e.max_hp);
    if (isBuilding(e)) {
        if (ud.footprint)     out.push('Размер: ' + ud.footprint.join('×'));
        if (ud.food_provided) out.push('Еда: +' + ud.food_provided);
        if (e.under_construction) out.push('<i>Строится…</i>');
        if (e.production_queue && e.production_queue.length) {
            out.push('');
            out.push('<b>Очередь:</b>');
            for (var i = 0; i < e.production_queue.length; i++) {
                var it = e.production_queue[i];
                var left = (it.time_left != null) ? it.time_left.toFixed(1) : '?';
                out.push((i+1) + '. ' + it.unit_type + ' (' + left + 's)');
            }
        }
    } else {
        if (ud.attack != null)
            out.push('Атака: ' + ud.attack + ' (' + (ud.attack_type||'-') + ')');
        if (ud.range  != null) out.push('Дальность: ' + ud.range);
        if (ud.armor  != null)
            out.push('Броня: ' + ud.armor + ' (' + (ud.armor_type||'-') + ')');
        if (ud.speed  != null) out.push('Скорость: ' + ud.speed);
    }
    var html = out.join('<br>');
    if (html === _lastInfoHtml) return;
    _lastInfoHtml = html;
    els.info.innerHTML = html;
}

function renderInfoMulti(sel) {
    var counts = {};
    for (var i = 0; i < sel.length; i++) {
        counts[sel[i].type_id] = (counts[sel[i].type_id] || 0) + 1;
    }
    var out = ['<b>Выделение</b>'];
    for (var k in counts) {
        var ud = (STATE.units_data || {})[k] || {};
        out.push((ud.name || k) + ' × ' + counts[k]);
    }
    els.info.innerHTML = out.join('<br>');
}

// ---------- Сетка команд ----------

function renderCommands(sel) {
    if (!els.cmd) return;

    var subject0 = sel.length ? sel[0] : null;
    if (currentMenu) {
        if (!subject0) {
            currentMenu = null;
        } else {
            var d0 = (isBuilding(subject0) ? STATE.buildings_data : STATE.units_data) || {};
            var u0 = d0[subject0.type_id] || {};
            var abils0 = u0.abilities || [];
            if (abils0.indexOf(currentMenu) === -1) {
                currentMenu = null;
            }
        }
    }
    var abilitiesData = (STATE && STATE.abilities_data) || {};
    var subject = sel.length ? sel[0] : null;

    var cmds = [];
    if (currentMenu && subject) {
        cmds = buildMenuCommands(subject, currentMenu, abilitiesData);
    } else if (subject) {
        cmds = buildPrimaryCommands(subject, abilitiesData);
    }

    // дедупликация: сравним новые кнопки с предыдущими
    var sig = JSON.stringify(cmds.map(function (c) {
        return [c.label, c.hot];
    }));
    if (sig === _lastCmdsJson) return;
    _lastCmdsJson = sig;

    els.cmd.innerHTML = '';
    paintCommands(cmds);
}

function buildPrimaryCommands(subject, abilitiesData) {
    var data = (isBuilding(subject) ? STATE.buildings_data : STATE.units_data) || {};
    var ud = data[subject.type_id] || {};
    var abilityIds = ud.abilities || [];
    var cmds = [];
    for (var i = 0; i < abilityIds.length && cmds.length < 9; i++) {
        var id = abilityIds[i];
        var ab = abilitiesData[id];
        if (!ab) continue;
        cmds.push(makeAbilityCmd(subject, id, ab));
    }
    return cmds;
}

function buildMenuCommands(subject, menuId, abilitiesData) {
    var ab = abilitiesData[menuId];
    if (!ab) { currentMenu = null; return buildPrimaryCommands(subject, abilitiesData); }
    var opts = ab.options || [];
    var cmds = [{
        label: '← Назад', hot: 'Esc',
        onClick: function () { currentMenu = null; updatePanels(); }
    }];
    for (var i = 0; i < opts.length && cmds.length < 9; i++) {
        (function (o) {
            var cost = o.cost || {};
            var price = '';
            if (cost.gold) price += cost.gold + 'g';
            if (cost.wood) price += (price ? '/' : '') + cost.wood + 'w';
            if (cost.food) price += (price ? '/' : '') + cost.food + 'f';
            cmds.push({
                label: (o.name || o.id) + (price ? ' · ' + price : ''),
                hot: String(o.hotkey || '?').toUpperCase(),
                onClick: function () { chooseOption(subject, menuId, o); }
            });
        })(opts[i]);
    }
    return cmds;
}

function makeAbilityCmd(subject, abilityId, ab) {
    return {
        label: ab.name || abilityId,
        hot: String(ab.hotkey || '?').toUpperCase(),
        onClick: function () { triggerAbility(abilityId, ab); }
    };
}

function triggerAbility(abilityId, ab) {
    if (!ab) return;
    if (ab.kind === 'menu') {
        currentMenu = abilityId;
        updatePanels();
        return;
    }
    if (ab.kind === 'spell') {
        toast('Способности в разработке');
        return;
    }
    if (ab.kind !== 'order') return;

    var ids = selIds();
    if (!ids.length) return;

    if (abilityId === 'stop' || abilityId === 'hold') {
        sendUseAbility(abilityId, {ids: ids});
        return;
    }
    if (abilityId === 'move') {
        mode = 'ability-point'; pendingAbility = 'move';
        setHint('Движение: клик по карте');
        return;
    }
    if (abilityId === 'attack_move') {
        mode = 'ability-point'; pendingAbility = 'attack_move';
        setHint('Атака-марш: клик по точке');
        return;
    }
    if (abilityId === 'attack') {
        mode = 'ability-enemy'; pendingAbility = 'attack';
        setHint('Атака: клик по врагу');
        return;
    }
    if (abilityId === 'gather') {
        mode = 'ability-resource'; pendingAbility = 'gather';
        setHint('Добыча: клик по золотой шахте');
        return;
    }
    if (abilityId === 'repair') {
        mode = 'ability-own-building'; pendingAbility = 'repair';
        setHint('Ремонт: клик по своему зданию');
        return;
    }
    if (abilityId === 'patrol') {
        mode = 'patrol'; patrolFirst = null;
        setHint('Патруль: точка 1');
        return;
    }
}

function chooseOption(subject, menuId, opt) {
    if (opt.target === 'none') {
        sendUseAbility(menuId, {ids: [subject.id], option: opt.id});
        currentMenu = null;
        updatePanels();
        return;
    }
    if (opt.target === 'point') {
        mode = 'build-ability';
        pendingAbility = menuId;
        pendingOption = opt.id;
        setHint('Строительство ' + (opt.name || opt.id) + ': клик по карте');
        currentMenu = null;
        updatePanels();
        return;
    }
}

function paintCommands(cmds) {
    for (var n = 0; n < 9; n++) {
        var cell = document.createElement('div');
        var c = cmds[n];
        cell.className = 'cmd' + (c ? '' : ' disabled');
        if (c) {
            cell.innerHTML = '<span class="hot">' + c.hot + '</span>'
                           + '<span class="lbl">' + c.label + '</span>';
            cell.addEventListener('click', c.onClick);
        }
        els.cmd.appendChild(cell);
    }
}

// ---------- Лобби ----------

function updateLobbyOverlay(st) {
    var overlay = document.getElementById('lobby-overlay');
    if (!overlay) return;
    if (!st || st.phase !== 'lobby') {
        overlay.style.display = 'none';
        return;
    }
    overlay.style.display = 'flex';

    var list = document.getElementById('lobby-players');
    var races = st.player_races || [];
    var names = st.names || [];
    var occupied = st.occupied || [];
    var html = '';
    for (var i = 0; i < names.length; i++) {
        var cls = occupied[i] ? '' : ' empty';
        var race = races[i] ? races[i] : '— не выбрана';
        html += '<div class="lobby-row' + cls + '">'
              + '<span class="nm">' + names[i] + '</span>'
              + '<span class="rc">' + race + '</span></div>';
    }
    list.innerHTML = html;

    var myR = st.my_race;
    var btns = document.querySelectorAll('#race-buttons button');
    for (var k = 0; k < btns.length; k++) {
        var on = btns[k].getAttribute('data-race') === myR;
        btns[k].style.outline = on ? '2px solid #5bd45b' : 'none';
    }
}

function bindLobby() {
    var btns = document.querySelectorAll('#race-buttons button');
    for (var i = 0; i < btns.length; i++) {
        btns[i].addEventListener('click', function () {
            var race = this.getAttribute('data-race');
            if (client && client.action) {
                client.action('set_race', {race: race}).catch(function (e) {
                    toast((e && e.message) || 'Ошибка выбора расы');
                });
            }
        });
    }
}

// ---------- Таймер ----------

function updateTimer() {
    if (!els.timer) return;
    if (!STATE) { els.timer.textContent = '00:00'; return; }
    var t = (STATE.tick || 0) / 10;
    var m = Math.floor(t / 60);
    var s = Math.floor(t % 60);
    els.timer.textContent = (m < 10 ? '0' : '') + m + ':'
                          + (s < 10 ? '0' : '') + s;
}

function setOnline(on) {
    if (els.dot) els.dot.classList.toggle('on', !!on);
}

// ---------- GameClient ----------

function onState(st) {
    STATE = st;

    var live = {};
    var list = st.entities || [];
    for (var i = 0; i < list.length; i++) live[list[i].id] = true;
    for (var k in selection) if (!live[k]) delete selection[k];
    for (var hk in hotkeys) {
        hotkeys[hk] = hotkeys[hk].filter(function (id) { return live[id]; });
    }

    updatePanels();
    updateTimer();
    updateLobbyOverlay(st);
    updateVictoryOverlay(st);
}

function updateVictoryOverlay(st) {
    var ov = document.getElementById('victory-overlay');
    if (!ov) return;
    if (!st || st.phase !== 'over') {
        ov.classList.remove('show');
        return;
    }
    ov.classList.add('show');
    var res = st.result || {};
    var mySlotId = st.slot;
    var win = (res.winners || []).indexOf(mySlotId) !== -1;
    var title = document.getElementById('vtitle');
    var sub = document.getElementById('vsub');
    title.textContent = win ? 'Победа!' : 'Поражение';
    title.className = win ? 'win' : 'lose';
    var winnerNames = (res.winners || []).map(function (i) {
        return (st.names || [])[i] || ('Игрок ' + (i+1));
    }).join(', ');
    sub.textContent = 'Победители: ' + (winnerNames || '—');
}

function onConnect()    { setOnline(true);  console.log('[rts] SSE ok'); }
function onDisconnect(r){ setOnline(false); console.log('[rts] off:', r); }

// ---------- Main loop ----------

var lastT = 0;
function frame(t) {
    var dt = lastT ? Math.min(0.05, (t - lastT) / 1000) : 0.016;
    lastT = t;
    updateCameraFromKeys(dt);
    draw();
    updateTimer();
    requestAnimationFrame(frame);
}

// ---------- Boot ----------

function cacheEls() {
    canvas = document.getElementById('game');
    if (canvas) ctx = canvas.getContext('2d');
    mmCanvas = document.getElementById('minimap');
    if (mmCanvas) mmCtx = mmCanvas.getContext('2d');
    portrait = document.getElementById('portrait');
    if (portrait) pctx = portrait.getContext('2d');
    root = document.getElementById('rts-root');
    els = {
        gold:         document.getElementById('res-gold'),
        wood:         document.getElementById('res-wood'),
        food:         document.getElementById('res-food'),
        selCount:     document.getElementById('sel-count'),
        hint:         document.getElementById('hint'),
        timer:        document.getElementById('timer'),
        portraitName: document.getElementById('portrait-name'),
        cmd:          document.getElementById('cmd-grid'),
        info:         document.getElementById('info-panel'),
        toast:        document.getElementById('toast'),
        dot:          document.getElementById('dot')
    };
}

function bindEvents() {
    window.addEventListener('resize', function () { sizeCanvas(); clampCam(); });
    if (typeof ResizeObserver === 'function') {
        try {
            new ResizeObserver(function () { sizeCanvas(); clampCam(); }).observe(root);
        } catch (e) {}
    }
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', function () { keys = {}; });
    if (canvas) {
        canvas.addEventListener('mousedown', onMouseDown);
        canvas.addEventListener('mousemove', onMouseMove);
        window.addEventListener('mouseup', onMouseUp);
        canvas.addEventListener('wheel', onWheel, {passive: false});
        canvas.addEventListener('contextmenu', onContextMenu);
    }
    if (mmCanvas) mmCanvas.addEventListener('mousedown', onMinimapDown);
}

function boot() {
    cacheEls();
    if (!canvas || !root) { console.error('[rts] нет #game'); return; }
    sizeCanvas();
    bindEvents();
    loadCam();
    if (!localStorage.getItem(LS_CAM)) {
        var ms = mapSize();
        cam.x = ms[0] / 2 - 10;
        cam.y = ms[1] / 2 - 8;
    }
    clampCam();

    if (typeof window.GameClient !== 'function') {
        console.error('[rts] GameClient не найден');
        return;
    }

    try {
        client = window.GameClient('rts', {
            realtime: true,
            stateUrl: '/rts/api/state',
            actionUrl: '/rts/api/action',
            onState:      onState,
            onConnect:    onConnect,
            onDisconnect: onDisconnect,
            onError:      function (m) { console.warn('[rts]', m); }
        });
        bindLobby();
        client.init();
    } catch (e) {
        console.error('[rts] GameClient:', e);
        return;
    }

    // отладка: __rts.dump() в консоли
    window.__rts = {
        get state() { return STATE; },
        get selection() { return selection; },
        get abilityHotkeyMap() { return abilityHotkeyMap; },
        get currentMenu() { return currentMenu; },
        dump: function () {
            if (!STATE) { console.log('STATE null'); return; }
            var s = selArray();
            console.log('phase        :', STATE.phase);
            console.log('tick         :', STATE.tick);
            console.log('slot         :', STATE.slot);
            console.log('entities     :', (STATE.entities || []).length);
            console.log('has abilities_data:', !!STATE.abilities_data);
            console.log('abilities keys:', Object.keys(STATE.abilities_data || {}).length);
            if (s.length) {
                var d = (isBuilding(s[0]) ? STATE.buildings_data : STATE.units_data) || {};
                var ud = d[s[0].type_id] || {};
                console.log('subject      :', s[0].type_id);
                console.log('subject abils:', ud.abilities);
                console.log('hotkey map   :', Object.keys(abilityHotkeyMap));
            }
        }
    };

    requestAnimationFrame(frame);
}

window.addEventListener('load', boot);

})();
