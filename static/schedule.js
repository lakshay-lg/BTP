(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var $ = function (id) { return document.getElementById(id); };
  var lastInput = null, lastOverrides = null, timer = null;

  function el(tag, attrs, text) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs || {}) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }
  function h(tag, attrs, text) {
    var n = document.createElement(tag);
    for (var k in attrs || {}) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }
  function money(n) {
    n = Number(n) || 0;
    var a = Math.abs(n), s = n < 0 ? '-' : '';
    if (a >= 1e7) return s + '₹' + (a / 1e7).toFixed(2) + ' Cr';
    if (a >= 1e5) return s + '₹' + (a / 1e5).toFixed(2) + ' L';
    return s + '₹' + a.toLocaleString('en-IN', { maximumFractionDigits: 0 });
  }
  function dt(iso) { var p = iso.split('-'); return Date.UTC(+p[0], +p[1] - 1, +p[2]); }
  function dayDiff(a, b) { return Math.round((dt(b) - dt(a)) / 864e5); }
  function fmtDate(iso) {
    var d = new Date(dt(iso));
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });
  }
  function monthLabel(m) {
    var p = m.split('-');
    return new Date(Date.UTC(+p[0], +p[1] - 1, 1)).toLocaleDateString('en-GB', { month: 'short', year: '2-digit', timeZone: 'UTC' });
  }
  function monthLong(m) {
    var p = m.split('-');
    return new Date(Date.UTC(+p[0], +p[1] - 1, 1)).toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' });
  }
  function niceMax(v) {
    if (v <= 0) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }
  function setStatus(msg, err) { var s = $('status'); s.textContent = msg; s.className = 'sched-status' + (err ? ' error' : ''); }

  /* ---------- server calls ---------- */
  function settings() {
    return {
      start_date: $('startDate').value,
      workweek_days: +$('workweek').value,
      cashflow: {
        retention_pct: +$('retention').value || 0,
        payment_lag_months: +$('lag').value || 0,
        mobilisation_advance_pct: +$('advance').value || 0,
        indirect_pct: +$('indirect').value || 0
      }
    };
  }
  function run() {
    if (!lastInput) return;
    var body = settings();
    body.activities_json = lastInput;
    if (lastOverrides) body.overrides = lastOverrides;
    $('runDemo').disabled = true;
    setStatus('Calculating…');
    fetch('/api/schedule/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'Request failed');
        render(res.j);
      })
      .catch(function (e) { setStatus('Could not build the schedule: ' + e.message, true); })
      .then(function () { $('runDemo').disabled = false; });
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(run, 250); }

  $('runDemo').addEventListener('click', function () {
    fetch('/api/schedule/demo-input').then(function (r) { return r.json(); })
      .then(function (j) { lastInput = j; lastOverrides = null; run(); })
      .catch(function (e) { setStatus('Could not load the demo: ' + e.message, true); });
  });
  $('runTwoTank').addEventListener('click', function () {
    fetch('/api/schedule/demo-two-tank').then(function (r) { return r.json(); })
      .then(function (j) { lastInput = j.input; lastOverrides = j.overrides; $('startDate').value = j.start_date; $('retention').value = 0; $('advance').value = 0; run(); })
      .catch(function (e) { setStatus('Could not load the demo: ' + e.message, true); });
  });
  $('precedenceFile').addEventListener('change', function (ev) {
    var f = ev.target.files[0]; if (!f) return;
    f.text().then(function (t) { lastInput = JSON.parse(t); lastOverrides = null; run(); })
      .catch(function () { setStatus('That file is not valid JSON.', true); });
  });
  ['startDate', 'workweek', 'retention', 'lag', 'advance', 'indirect'].forEach(function (id) {
    $(id).addEventListener('change', schedule);
  });
  document.querySelectorAll('.tabs button').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('.tabs button').forEach(function (x) { x.classList.toggle('active', x === b); });
      document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.toggle('active', p.id === 'tab-' + b.dataset.tab); });
    });
  });

  /* ---------- rendering ---------- */
  function render(d) {
    var rep = d._reports || {}, c = d.cost_summary || {};
    var priced = rep.costs ? rep.costs.priced + '/' + rep.costs.total : '';
    setStatus('Schedule built: ' + d.activities.length + ' activities, ' + d.project_duration_days + ' working days.');
    var banner = $('banner');
    var notes = [];
    if (d._note) notes.push(d._note);
    if (rep.productivity && rep.productivity.not_exact && rep.productivity.not_exact.length)
      notes.push(rep.productivity.not_exact.length + ' activities use near/proxy productivity (' + rep.productivity.not_exact.map(function (n) { return n.activity_id; }).join(', ') + ').');
    if (rep.productivity && rep.productivity.missing && rep.productivity.missing.length)
      notes.push('No productivity for: ' + rep.productivity.missing.map(function (n) { return n.activity_id; }).join(', ') + '.');
    banner.textContent = notes.join(' ');
    banner.classList.toggle('hidden', !notes.length);

    var m = $('metrics'); m.textContent = '';
    [['Project dates', fmtDate(d.project_start_date), 'to ' + fmtDate(d.project_end_date)],
     ['Duration', d.project_duration_days + ' working days', d.workweek_days + '-day week'],
     ['Total work value', money(c.total_work_value), priced + ' activities priced'],
     ['Peak spend month', c.peak_spend_month ? monthLong(c.peak_spend_month) : '–', c.peak_spend_value != null ? money(c.peak_spend_value) : ''],
     ['Peak funding need', money(c.peak_funding_requirement), c.peak_funding_month ? 'in ' + monthLong(c.peak_funding_month) : '']
    ].forEach(function (x) {
      var a = h('article'); a.appendChild(h('small', null, x[0])); a.appendChild(h('strong', null, x[1])); a.appendChild(h('span', null, x[2])); m.appendChild(a);
    });
    m.classList.remove('hidden');
    $('results').classList.remove('hidden');
    gantt(d); monthly(d); scurve(d); cash(d); table(d);
  }

  function ticks(max, n) { var t = [], step = niceMax(max) / n; for (var i = 0; i <= n; i++) t.push(step * i); return t; }

  function gantt(d) {
    var box = $('ganttBox'); box.textContent = '';
    var acts = d.activities.slice().sort(function (a, b) { return dt(a.start_date) - dt(b.start_date) || a.activity_id.localeCompare(b.activity_id, undefined, { numeric: true }); });
    var p0 = d.project_start_date, total = dayDiff(p0, d.project_end_date) + 1;
    var px = Math.max(5, Math.min(14, Math.floor(1000 / total)));
    var labelW = 250, top = 44, rowH = 28, W = labelW + total * px + 20, H = top + acts.length * rowH + 10;
    var svg = el('svg', { width: W, height: H, role: 'img', 'aria-label': 'Gantt chart' });
    // month header + grid
    var cur = new Date(dt(p0)), endT = dt(d.project_end_date);
    cur = new Date(Date.UTC(cur.getUTCFullYear(), cur.getUTCMonth(), 1));
    while (cur.getTime() <= endT) {
      var iso = cur.toISOString().slice(0, 10), x = labelW + Math.max(0, dayDiff(p0, iso)) * px;
      svg.appendChild(el('line', { x1: x, x2: x, y1: top - 22, y2: H, stroke: '#dce3e2' }));
      svg.appendChild(el('text', { x: x + 4, y: top - 8, 'class': 't b' }, monthLabel(iso.slice(0, 7))));
      cur = new Date(Date.UTC(cur.getUTCFullYear(), cur.getUTCMonth() + 1, 1));
    }
    var pos = {};
    acts.forEach(function (a, i) {
      var y = top + i * rowH, x = labelW + dayDiff(p0, a.start_date) * px, w = (dayDiff(a.start_date, a.end_date) + 1) * px;
      pos[a.activity_id] = { x: x, w: w, y: y + rowH / 2 };
      if (i % 2) svg.appendChild(el('rect', { x: 0, y: y, width: W, height: rowH, fill: '#f8faf9' }));
      var label = a.activity_id + '  ' + (a.description || a.activity_class || '');
      svg.appendChild(el('text', { x: 6, y: y + rowH / 2 + 4, 'class': 't lbl' }, label.length > 36 ? label.slice(0, 35) + '…' : label));
    });
    // dependency links first (under bars)
    acts.forEach(function (a) {
      (a.predecessors || []).forEach(function (pid) {
        var p = pos[pid], s = pos[a.activity_id]; if (!p || !s) return;
        var x1 = p.x + p.w, x2 = s.x, xm = Math.max(x1 + 4, x2 - 4);
        svg.appendChild(el('path', { d: 'M' + x1 + ',' + p.y + ' H' + xm + ' V' + s.y + ' H' + x2, fill: 'none', stroke: '#9fb3b1', 'stroke-width': 1, opacity: .8 }));
        svg.appendChild(el('path', { d: 'M' + x2 + ',' + s.y + ' l-4,-3 v6 z', fill: '#9fb3b1' }));
      });
    });
    acts.forEach(function (a) {
      var s = pos[a.activity_id], bar = el('rect', { x: s.x, y: s.y - 8, width: Math.max(3, s.w), height: 16, rx: 2, fill: a.critical ? '#e8a735' : '#0e7c7b' });
      bar.appendChild(el('title', {}, a.activity_id + ' · ' + (a.description || '') + '\n' + fmtDate(a.start_date) + ' → ' + fmtDate(a.end_date) +
        '\n' + a.duration_days + ' working days · float ' + a.total_float + ' d' + '\nQty ' + a.quantity + ' ' + (a.unit || '') + (a.cost != null ? ' · ' + money(a.cost) : '')));
      svg.appendChild(bar);
      var txt = a.duration_days + 'd';
      if (s.w >= 26) svg.appendChild(el('text', { x: s.x + s.w / 2, y: s.y + 4, 'text-anchor': 'middle', style: 'font:10px DM Mono,monospace;fill:#fff', 'pointer-events': 'none' }, txt));
      else svg.appendChild(el('text', { x: s.x + s.w + 5, y: s.y + 4, 'class': 't' }, txt));
    });
    box.appendChild(svg);
  }

  function barChart(box, months, series, opts) {
    box.textContent = '';
    var W = Math.max(640, months.length * (series.length > 1 ? 90 : 70) + 90), H = 300, l = 74, b = 34, t = 14, r = 12;
    var max = niceMax(Math.max.apply(null, [1].concat(series.map(function (s) { return Math.max.apply(null, s.values); }))));
    var svg = el('svg', { width: W, height: H, role: 'img', 'aria-label': opts.label });
    ticks(max, 4).forEach(function (v) {
      var y = H - b - (v / max) * (H - b - t);
      svg.appendChild(el('line', { x1: l, x2: W - r, y1: y, y2: y, stroke: '#e9eeee' }));
      svg.appendChild(el('text', { x: l - 6, y: y + 3, 'text-anchor': 'end', 'class': 't' }, money(v)));
    });
    var gw = (W - l - r) / months.length, bw = Math.min(34, gw * 0.7 / series.length);
    months.forEach(function (m, i) {
      var gx = l + gw * i + (gw - bw * series.length) / 2;
      series.forEach(function (s, k) {
        var v = s.values[i], hgt = (v / max) * (H - b - t);
        var rc = el('rect', { x: gx + k * bw, y: H - b - hgt, width: bw - 2, height: Math.max(0, hgt), fill: s.color, rx: 2 });
        rc.appendChild(el('title', {}, monthLabel(m) + ' · ' + s.name + ': ' + money(v)));
        svg.appendChild(rc);
      });
      svg.appendChild(el('text', { x: l + gw * i + gw / 2, y: H - b + 16, 'text-anchor': 'middle', 'class': 't' }, monthLabel(m)));
    });
    svg.appendChild(el('line', { x1: l, x2: W - r, y1: H - b, y2: H - b, stroke: '#cfdad9' }));
    box.appendChild(svg);
    if (series.length > 1) {
      var lg = h('p', { 'class': 'sched-legend' });
      series.forEach(function (s) { var sp = h('span'); sp.style.cssText = 'display:inline-flex;align-items:center'; var sw = h('span', { 'class': 'lg' }); sw.style.background = s.color; sp.appendChild(sw); sp.appendChild(document.createTextNode(s.name)); lg.appendChild(sp); });
      box.appendChild(lg);
    }
  }

  function monthly(d) {
    var months = d.monthly.map(function (m) { return m.month; });
    barChart($('monthlyBox'), months, [{ name: 'Work value', color: '#0e7c7b', values: d.monthly.map(function (m) { return m.work_value; }) }], { label: 'Monthly work value' });
    var wrap = $('allocTable'); wrap.textContent = '';
    var tbl = h('table'), head = h('tr');
    head.appendChild(h('th', null, 'Activity'));
    months.forEach(function (m) { head.appendChild(h('th', { 'class': 'num' }, monthLabel(m))); });
    head.appendChild(h('th', { 'class': 'num' }, 'Total'));
    tbl.appendChild(head);
    d.activities.forEach(function (a) {
      var tr = h('tr'); tr.appendChild(h('td', null, a.activity_id + ' ' + (a.description || '')));
      months.forEach(function (m) { var v = (a.monthly_cost || {})[m]; tr.appendChild(h('td', { 'class': 'num' }, v ? money(v) : '–')); });
      tr.appendChild(h('td', { 'class': 'num' }, a.cost != null ? money(a.cost) : '–')); tbl.appendChild(tr);
    });
    var tot = h('tr'); tot.appendChild(h('td', null, 'Total'));
    d.monthly.forEach(function (m) { tot.appendChild(h('td', { 'class': 'num' }, money(m.work_value))); });
    tot.appendChild(h('td', { 'class': 'num' }, money(d.cost_summary.total_work_value))); tot.style.fontWeight = '700';
    tbl.appendChild(tot); wrap.appendChild(tbl);
  }

  function lineChart(box, months, ys, opts) {
    box.textContent = '';
    var W = Math.max(640, months.length * 80 + 90), H = 300, l = 74, b = 34, t = 14, r = 20;
    var lo = Math.min(0, Math.min.apply(null, ys)), hi = Math.max.apply(null, ys.concat([opts.min || 1]));
    if (opts.fixedMax) hi = opts.fixedMax;
    var range = niceMax(Math.max(hi, -lo)); hi = lo < 0 ? range : range; lo = lo < 0 ? -range : 0;
    if (opts.fixedMax) { hi = opts.fixedMax; lo = 0; }
    var Y = function (v) { return t + (hi - v) / (hi - lo) * (H - b - t); };
    var gw = (W - l - r) / months.length, X = function (i) { return l + gw * (i + 0.5); };
    var svg = el('svg', { width: W, height: H, role: 'img', 'aria-label': opts.label });
    var step = (hi - lo) / 4;
    for (var i = 0; i <= 4; i++) { var v = lo + step * i, y = Y(v);
      svg.appendChild(el('line', { x1: l, x2: W - r, y1: y, y2: y, stroke: '#e9eeee' }));
      svg.appendChild(el('text', { x: l - 6, y: y + 3, 'text-anchor': 'end', 'class': 't' }, opts.fmt(v))); }
    if (lo < 0) svg.appendChild(el('line', { x1: l, x2: W - r, y1: Y(0), y2: Y(0), stroke: '#8fa2a0' }));
    var pts = ys.map(function (v, i) { return X(i) + ',' + Y(v); });
    if (opts.origin != null) pts.unshift(l + ',' + Y(opts.origin));
    if (lo === 0) svg.appendChild(el('polygon', { points: pts.join(' ') + ' ' + X(ys.length - 1) + ',' + Y(0) + (opts.origin != null ? ' ' + l + ',' + Y(0) : ''), fill: opts.area || '#0e7c7b', opacity: .12 }));
    svg.appendChild(el('polyline', { points: pts.join(' '), fill: 'none', stroke: opts.color || '#0e7c7b', 'stroke-width': 2.5 }));
    ys.forEach(function (v, i) {
      var c = el('circle', { cx: X(i), cy: Y(v), r: 4, fill: '#fff', stroke: opts.color || '#0e7c7b', 'stroke-width': 2 });
      c.appendChild(el('title', {}, monthLabel(months[i]) + ': ' + opts.fmt(v))); svg.appendChild(c);
      svg.appendChild(el('text', { x: X(i), y: H - b + 16, 'text-anchor': 'middle', 'class': 't' }, monthLabel(months[i])));
    });
    if (opts.mark != null) {
      var mi = opts.mark, mv = ys[mi];
      svg.appendChild(el('text', { x: X(mi), y: Y(mv) + (mv < 0 ? 20 : -10), 'text-anchor': 'middle', 'class': 't b' }, opts.markLabel));
    }
    box.appendChild(svg);
  }

  function scurve(d) {
    var months = d.monthly.map(function (m) { return m.month; });
    lineChart($('sBox'), months, d.monthly.map(function (m) { return m.cumulative_percent; }),
      { label: 'S-curve of cumulative work value', fmt: function (v) { return Math.round(v) + '%'; }, fixedMax: 100, origin: 0 });
  }

  function cash(d) {
    var months = d.monthly.map(function (m) { return m.month; });
    barChart($('cashBox'), months, [
      { name: 'Expenditure', color: '#e8a735', values: d.monthly.map(function (m) { return m.expenditure; }) },
      { name: 'Receipts', color: '#0e7c7b', values: d.monthly.map(function (m) { return m.receipt; }) }], { label: 'Expenditure and receipts' });
    var ys = d.monthly.map(function (m) { return m.cumulative_net_cash; });
    var mi = ys.indexOf(Math.min.apply(null, ys));
    lineChart($('netBox'), months, ys, { label: 'Cumulative net cash', fmt: money, color: '#c9574f', area: '#c9574f', mark: ys[mi] < 0 ? mi : null, markLabel: 'Peak funding ' + money(-ys[mi]) });
  }

  function table(d) {
    var wrap = $('actTable'); wrap.textContent = '';
    var tbl = h('table'), head = h('tr');
    ['ID', 'Activity', 'Qty', 'Predecessors', 'Days', 'Start', 'End', 'Float', 'Cost', 'Productivity'].forEach(function (x, i) { head.appendChild(h('th', i === 2 || i === 4 || i === 7 || i === 8 ? { 'class': 'num' } : null, x)); });
    tbl.appendChild(head);
    d.activities.forEach(function (a) {
      var tr = h('tr', a.critical ? { 'class': 'crit' } : null);
      [a.activity_id, a.description || a.activity_class, a.quantity + ' ' + (a.unit || ''), (a.predecessors || []).join(', ') || '–', a.duration_days, fmtDate(a.start_date), fmtDate(a.end_date), a.total_float, a.cost != null ? money(a.cost) : '–',
        a.productivity ? a.productivity.match_quality + ' · ' + a.productivity.source : 'missing'].forEach(function (x, i) {
        tr.appendChild(h('td', i === 2 || i === 4 || i === 7 || i === 8 ? { 'class': 'num' } : null, String(x)));
      });
      tbl.appendChild(tr);
    });
    wrap.appendChild(tbl);
  }
}());
