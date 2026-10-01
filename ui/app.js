/* Intel Virtual Display UI. Talks to the exe's local API (token-protected). No dependencies. */
(function () {
    'use strict';

    // The exe opens this page as http://127.0.0.1:<port>/#<token>. Keep the token for this window, hide it from the URL.
    var token = location.hash.slice(1) || sessionStorage.getItem('ivd-token') || '';
    if (location.hash) { sessionStorage.setItem('ivd-token', token); history.replaceState(null, '', location.pathname); }

    // Preset desktop sizes as multiples of the native size (1920x1080 -> 2560x1440 ... 7680x4320).
    var FACTORS = [4 / 3, 5 / 3, 2, 8 / 3, 4];
    var POLL_MS = 1500;

    var els = {
        displays: document.getElementById('displays'),
        keep: document.getElementById('keep'),
        startup: document.getElementById('startup'),
        log: document.getElementById('log'),
        version: document.getElementById('version'),
        connection: document.getElementById('connection'),
        toasts: document.getElementById('toasts'),
        dialog: document.getElementById('confirm'),
        confirmSize: document.getElementById('confirmSize'),
        confirmLeft: document.getElementById('confirmLeft'),
        confirmBar: document.getElementById('confirmBar'),
        keepBtn: document.getElementById('keepBtn'),
        revertBtn: document.getElementById('revertBtn')
    };

    var state = null, lastDisplaysSig = '', lastLogSig = '', busy = false, countdown = null, drafts = {};

    // ---------- helpers ----------
    function h(tag, attrs, children) {
        var el = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (k) {
            var v = attrs[k];
            if (v === false || v === null || v === undefined) return;
            if (k === 'text') el.textContent = v;
            else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
            else el.setAttribute(k, v === true ? '' : v);
        });
        (children || []).forEach(function (c) { if (c) el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
        return el;
    }
    function svg(tag, attrs) {
        var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
        Object.keys(attrs).forEach(function (k) { if (k === 'text') el.textContent = attrs[k]; else el.setAttribute(k, attrs[k]); });
        return el;
    }
    function even(v) { return Math.round(v / 2) * 2; }
    function size(w, ht) { return w + ' × ' + ht; }

    function api(path, body) {
        return fetch('/api/' + path, {
            method: body ? 'POST' : 'GET',
            headers: { 'X-IVD-Token': token, 'Content-Type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
            cache: 'no-store'
        }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
    }

    function toast(msg) {
        var t = h('div', { class: 'toast', text: msg });
        els.toasts.appendChild(t);
        setTimeout(function () { t.remove(); }, 6000);
    }

    // ---------- rendering ----------
    function diagram(d) {
        // Two rectangles at the same scale: the desktop Windows draws, and the panel it is shrunk onto.
        var W = 360, H = 170, pad = 6;
        var maxW = Math.max(d.desktopWidth, d.nativeWidth), maxH = Math.max(d.desktopHeight, d.nativeHeight);
        var s = Math.min((W - pad * 2) / maxW, (H - 30 - pad) / maxH);
        var dw = d.desktopWidth * s, dh = d.desktopHeight * s, nw = d.nativeWidth * s, nh = d.nativeHeight * s;
        var root = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'Desktop ' + size(d.desktopWidth, d.desktopHeight) + ' scaled onto a ' + size(d.nativeWidth, d.nativeHeight) + ' panel' });
        var base = H - 22;
        root.appendChild(svg('rect', { x: pad, y: base - dh, width: dw, height: dh, rx: 6, fill: 'none', stroke: '#d2462f', 'stroke-width': 1.2, 'stroke-dasharray': '5 4' }));
        root.appendChild(svg('rect', { x: pad, y: base - nh, width: nw, height: nh, rx: 4, fill: 'rgba(28,27,24,.06)', stroke: '#1c1b18', 'stroke-width': 1.2 }));
        root.appendChild(svg('text', { x: pad, y: H - 6, 'font-family': 'JetBrains Mono, monospace', 'font-size': 10, fill: '#8a857a', text: 'desktop ' + size(d.desktopWidth, d.desktopHeight) + '  ·  panel ' + size(d.nativeWidth, d.nativeHeight) }));
        return h('figure', { class: 'diagram' }, [root, h('figcaption', { text: d.desktopWidth > d.nativeWidth
            ? 'The dashed area is drawn by Windows and shrunk to fit the solid panel.'
            : d.desktopWidth < d.nativeWidth ? 'A smaller desktop is enlarged to fill the panel.' : 'Native: one desktop pixel per panel pixel.' })]);
    }

    function card(d) {
        var native = d.desktopWidth === d.nativeWidth && d.desktopHeight === d.nativeHeight;
        var factor = d.desktopWidth / d.nativeWidth;
        var isPending = state.pending && state.pending.key === d.key;

        var badges = h('div', { class: 'dcard__badges' }, [
            h('span', { class: 'badge', text: d.builtIn ? 'Built-in' : d.output }),
            h('span', { class: 'badge', text: d.vendor + ' GPU' }),
            d.primary ? h('span', { class: 'badge', text: 'Primary' }) : null,
            h('span', { class: 'badge', text: d.gdi.replace('\\\\.\\', '') })
        ]);

        var kpis = h('div', { class: 'kpis' }, [
            h('div', { class: 'kpi' }, [h('span', { class: 'kpi__label', text: 'Desktop' }), h('span', { class: 'kpi__val', text: size(d.desktopWidth, d.desktopHeight) })]),
            h('div', { class: 'kpi' }, [h('span', { class: 'kpi__label', text: 'Panel signal' }), h('span', { class: 'kpi__val' }, [size(d.nativeWidth, d.nativeHeight), h('small', { text: d.refresh + ' Hz' })])]),
            h('div', { class: 'kpi' }, [h('span', { class: 'kpi__label', text: 'Scale' }), h('span', { class: 'kpi__val', text: native ? 'Native' : (factor > 1 ? factor.toFixed(2).replace(/\.?0+$/, '') + '× down' : (1 / factor).toFixed(2).replace(/\.?0+$/, '') + '× up') })])
        ]);

        var sizes = [{ w: d.nativeWidth, h: d.nativeHeight, label: 'Native' }].concat(FACTORS.map(function (f) { return { w: even(d.nativeWidth * f), h: even(d.nativeHeight * f) }; }));
        var presets = h('div', { class: 'presets', role: 'group', 'aria-label': 'Desktop size for ' + d.name }, sizes.map(function (s) {
            var current = s.w === d.desktopWidth && s.h === d.desktopHeight;
            return h('button', { class: 'btn btn--sm', type: 'button', 'aria-pressed': current ? 'true' : 'false', disabled: busy,
                text: s.label ? s.label : size(s.w, s.h), title: size(s.w, s.h),
                onclick: function () { if (!current) apply(d, s.w, s.h); } });
        }));

        var draft = drafts[d.key] || { w: '', h: '' };
        var wIn = h('input', { class: 'input', type: 'number', inputmode: 'numeric', min: 640, max: 16384, step: 2, id: 'w-' + d.gdi, placeholder: String(d.nativeWidth * 2), value: draft.w });
        var hIn = h('input', { class: 'input', type: 'number', inputmode: 'numeric', min: 480, max: 16384, step: 2, id: 'h-' + d.gdi, placeholder: String(d.nativeHeight * 2), value: draft.h });
        function saveDraft() { drafts[d.key] = { w: wIn.value, h: hIn.value }; }
        wIn.addEventListener('input', saveDraft); hIn.addEventListener('input', saveDraft);
        var custom = h('form', { class: 'custom', onsubmit: function (e) {
            e.preventDefault();
            var w = parseInt(wIn.value, 10), ht = parseInt(hIn.value, 10);
            if (!(w >= 640 && ht >= 480)) { toast('Enter a width and height, at least 640 × 480.'); return; }
            apply(d, w, ht);
        } }, [
            h('div', { class: 'field' }, [h('label', { class: 'label', for: 'w-' + d.gdi, text: 'Width' }), wIn]),
            h('span', { class: 'x', 'aria-hidden': 'true', text: '×' }),
            h('div', { class: 'field' }, [h('label', { class: 'label', for: 'h-' + d.gdi, text: 'Height' }), hIn]),
            h('button', { class: 'btn btn--sm btn--ink', type: 'submit', disabled: busy, text: 'Apply' })
        ]);

        var status;
        if (isPending) status = h('p', { class: 'status' }, [h('span', { class: 'dot dot--warn' }), 'Waiting for you to keep or revert ' + size(state.pending.width, state.pending.height) + '.']);
        else if (d.remembered) status = h('p', { class: 'status' }, [h('span', { class: 'dot dot--ok' }), 'Remembered: ' + size(d.remembered.width, d.remembered.height) + (state.settings.keep ? ', re-applied when monitors change.' : '.')]);
        else status = h('p', { class: 'status' }, [h('span', { class: 'dot' }), 'Using the native resolution.']);

        var warn = null;
        if (!d.builtIn) warn = h('div', { class: 'banner banner--warn' }, [h('span', { text: 'External display: some drivers (including Intel) refuse desktops larger than native here. If Windows refuses, nothing changes.' })]);
        else if (!d.virtualModes) warn = h('div', { class: 'banner banner--warn' }, [h('span', { text: 'This driver does not report Windows virtual-mode support, so the app will ask the driver to scale instead. It may refuse.' })]);

        return h('section', { class: 'pane dcard', 'aria-label': d.name }, [
            h('div', { class: 'dcard__head' }, [h('h2', { class: 'dcard__name', text: d.name }), badges]),
            h('div', { class: 'dcard__body' }, [
                h('div', {}, [kpis, diagram(d)]),
                h('div', {}, [h('p', { class: 'label', text: 'Desktop size' }), presets, custom, status, warn])
            ])
        ]);
    }

    function render() {
        els.connection.hidden = true;
        els.version.textContent = 'Version ' + state.version;
        if (document.activeElement !== els.keep) els.keep.checked = state.settings.keep;
        if (document.activeElement !== els.startup) els.startup.checked = state.settings.startWithWindows;

        var sig = JSON.stringify([state.displays, state.pending && state.pending.key, state.settings.keep, busy]);
        var editing = document.activeElement && document.activeElement.tagName === 'INPUT' && els.displays.contains(document.activeElement);
        if (sig !== lastDisplaysSig && !editing) {
            lastDisplaysSig = sig;
            els.displays.replaceChildren.apply(els.displays, state.displays.length
                ? state.displays.map(card)
                : [h('div', { class: 'pane empty' }, [h('strong', { text: 'No displays found' }), 'Windows reports no active display.'])]);
        }
        var logSig = state.log.join('\n');
        if (logSig !== lastLogSig) {
            lastLogSig = logSig;
            els.log.replaceChildren.apply(els.log, state.log.map(function (l) { return h('div', { text: l }); }));
        }
        syncDialog();
    }

    // ---------- confirm dialog ----------
    function syncDialog() {
        var p = state.pending;
        if (!p) { if (els.dialog.open) els.dialog.close(); stopCountdown(); return; }
        els.confirmSize.textContent = size(p.width, p.height);
        if (!countdown) startCountdown(p.secondsLeft);
        else countdown.left = Math.min(countdown.left, p.secondsLeft);
        if (!els.dialog.open) { els.dialog.showModal(); els.keepBtn.focus(); }
    }
    function startCountdown(left) {
        countdown = { left: left, total: state.confirmSeconds };
        paintCountdown();
        countdown.timer = setInterval(function () { countdown.left = Math.max(0, countdown.left - 1); paintCountdown(); }, 1000);
    }
    function paintCountdown() {
        els.confirmLeft.textContent = countdown.left;
        els.confirmBar.style.transform = 'scaleX(' + (countdown.left / countdown.total) + ')';
    }
    function stopCountdown() { if (countdown) { clearInterval(countdown.timer); countdown = null; } }

    els.keepBtn.addEventListener('click', function () {
        api('confirm', {}).then(function (r) { if (!r.ok) toast(r.error); else toast('Kept. It will be restored after restarts.'); refresh(); });
    });
    els.revertBtn.addEventListener('click', revert);
    els.dialog.addEventListener('cancel', function (e) { e.preventDefault(); revert(); });   // Esc = revert
    function revert() { api('revert', {}).then(function () { toast('Reverted.'); refresh(); }); }

    // ---------- actions ----------
    function apply(d, w, ht) {
        if (busy) return;
        var native = w === d.nativeWidth && ht === d.nativeHeight;
        busy = true; render();
        toast('Switching ' + d.name + ' to ' + size(w, ht) + '…');
        api('apply', { key: d.key, width: w, height: ht, confirm: !native })
            .then(function (r) { if (!r.ok) toast(r.error); })
            .catch(function () { toast('The app did not respond.'); })
            .then(function () { busy = false; refresh(); });
    }

    els.keep.addEventListener('change', function () { api('settings', { keep: els.keep.checked }).then(refresh); });
    els.startup.addEventListener('change', function () {
        api('settings', { startWithWindows: els.startup.checked }).then(function () {
            toast(els.startup.checked ? 'The app will start with Windows.' : 'The app will no longer start with Windows.'); refresh();
        });
    });
    document.getElementById('openWindowsSettings').addEventListener('click', function () { api('open-settings', {}); });

    // ---------- polling ----------
    function refresh() {
        return api('state').then(function (s) { state = s; render(); })
            .catch(function () { els.connection.hidden = false; });
    }
    refresh();
    setInterval(function () { if (!busy) refresh(); }, POLL_MS);
})();
