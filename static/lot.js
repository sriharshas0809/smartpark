(() => {
  const L = window.LOT;
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const ICON = {CAR: 'Car', BIKE: 'Bike', EV: 'EV'};
  let state = null, route = null;

  function toast(msg, kind = 'info') {
    const t = document.createElement('div');
    t.className = 'toast ' + kind; t.textContent = msg;
    $('#toasts').appendChild(t); setTimeout(() => t.remove(), 5000);
  }
  async function post(url, body) {
    try {
      const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
      return await r.json();
    } catch (e) { return {ok: false, error: 'Could not reach the server. Check that it is running.'}; }
  }
  function show(el, title, pairs, cls = '') {
    el.innerHTML = '';
    const box = document.createElement('div'); box.className = 'result ' + cls;
    const h = document.createElement('strong'); h.textContent = title; box.appendChild(h);
    if (pairs && pairs.length) {
      const dl = document.createElement('dl');
      pairs.forEach(([k, v]) => {
        const dt = document.createElement('dt'), dd = document.createElement('dd');
        dt.textContent = k; dd.textContent = v; dl.append(dt, dd);
      });
      box.appendChild(dl);
    }
    el.appendChild(box); return box;
  }
  const fail = (el, j) => { show(el, j.error || 'Something went wrong', null, 'error'); toast(j.error || 'Something went wrong', 'error'); };

  async function refresh() {
    try { state = await (await fetch(`/api/lot/${L.id}/status`)).json(); } catch (e) { return; }
    renderStats(); renderGrid(); renderMine();
  }

  function renderStats() {
    const s = state.summary;
    $('#level').innerHTML = `<span class="tag ${s.level}">${s.level} demand &times;${s.multiplier}${s.auto ? '' : ' (set by admin)'}</span>`;
    const items = [['Free', s.free], ['Reserved', s.reserved], ['Occupied', s.occupied]];
    Object.entries(s.kinds).forEach(([k, v]) => items.push([k === 'EV' ? 'EV free' : k[0] + k.slice(1).toLowerCase() + ' free', v.free]));
    $('#stats').innerHTML = items.map(([a, b]) => `<div class="stat"><b>${b}</b>${a}</div>`).join('');
  }

  function renderGrid() {
    const g = $('#grid'); g.style.gridTemplateColumns = `repeat(${state.cols}, 1fr)`; g.innerHTML = '';
    const at = {}; state.slots.forEach((s) => (at[s.row + ',' + s.col] = s));
    const onRoute = new Set(route ? route.cells.map((c) => c.join(',')) : []);
    const target = route ? route.cells[route.cells.length - 1].join(',') : null;
    state.grid.forEach((row, r) => row.forEach((tok, c) => {
      const key = r + ',' + c, el = document.createElement('div');
      if (tok === 'G') { el.className = 'cell gate'; el.textContent = 'Gate'; }
      else if (at[key]) {
        const s = at[key];
        el.className = `cell slot ${s.state}${s.flagged ? ' flagged' : ''}${s.mine ? ' mine' : ''}${key === target ? ' target' : ''}`;
        el.tabIndex = 0; el.setAttribute('role', 'button');
        const label = s.flagged ? 'Sensor alert' : s.state.replace(/_/g, ' ').toLowerCase();
        el.setAttribute('aria-label', `Slot ${s.id}, ${ICON[s.kind]}, ${label}. Show route`);
        el.title = `${s.id} - ${ICON[s.kind]} - ${label}`;
        el.innerHTML = `${s.id}<small>${s.kind}</small>` + (s.plate ? `<small>${s.plate}</small>` : '');
        el.onclick = () => showRoute(s.id);
        el.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); showRoute(s.id); } };
      } else {
        el.className = 'cell aisle' + (onRoute.has(key) ? ' route' : '');
      }
      g.appendChild(el);
    }));
  }

  async function showRoute(sid) {
    const j = await (await fetch(`/api/lot/${L.id}/path/${sid}`)).json();
    if (!j.ok) { toast(j.error, 'error'); return; }
    route = j; renderGrid();
    $('#route-card').hidden = false;
    $('#route-title').textContent = `Route to slot ${sid} (${j.distance} blocks)`;
    $('#route-steps').innerHTML = j.directions.map((d) => `<li>${d}</li>`).join('');
  }

  function renderMine() {
    const box = $('#mine-box'); if (!box) return;
    const items = state.mine || []; box.hidden = items.length === 0;
    const el = $('#mine'); el.innerHTML = '';
    items.forEach((m) => {
      const row = document.createElement('div'); row.className = 'mine-item';
      const info = document.createElement('div');
      info.innerHTML = m.type === 'reservation'
        ? `<b>Slot ${m.slot}</b> reserved for <span class="mono">${m.plate}</span><br><span class="muted">${Math.ceil(m.minutes_left)} min left to arrive</span>`
        : `<b>Slot ${m.slot}</b> parked: <span class="mono">${m.plate}</span><br><span class="muted">Ticket ${m.ticket_id}</span>`;
      const actions = document.createElement('div');
      const b1 = document.createElement('button'); b1.className = 'btn small ghost'; b1.textContent = 'Route';
      b1.onclick = () => showRoute(m.slot); actions.appendChild(b1);
      if (m.type === 'reservation') {
        const b2 = document.createElement('button'); b2.className = 'btn small danger'; b2.textContent = 'Cancel';
        b2.onclick = async () => {
          const j = await post(`/api/lot/${L.id}/cancel`, {plate: m.plate});
          j.ok ? toast('Reservation cancelled', 'success') : toast(j.error, 'error'); refresh();
        };
        actions.append(' ', b2);
      } else {
        const b2 = document.createElement('button'); b2.className = 'btn small'; b2.textContent = 'Pay';
        b2.onclick = () => { selectTab('exit'); $('#form-exit [name=ticket]').value = m.ticket_id; };
        actions.append(' ', b2);
      }
      row.append(info, actions); el.appendChild(row);
    });
  }

  function selectTab(name) {
    $$('.tab').forEach((t) => t.setAttribute('aria-selected', t.dataset.tab === name));
    $$('.tabpanel').forEach((p) => (p.hidden = p.id !== 'tab-' + name));
  }
  $$('.tab').forEach((t) => (t.onclick = () => selectTab(t.dataset.tab)));

  const fd = (form) => Object.fromEntries(new FormData(form).entries());

  $('#form-reserve')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const j = await post(`/api/lot/${L.id}/reserve`, fd(e.target)), out = $('#out-reserve');
    if (!j.ok) return fail(out, j);
    show(out, 'Bay reserved', [['Slot', j.slot], ['Plate', j.plate], ['Held for', j.hold_minutes + ' minutes']]);
    toast(`Slot ${j.slot} reserved`, 'success'); await refresh(); showRoute(j.slot);
  });

  const noise = $('#form-entry [name=noise]');
  noise?.addEventListener('input', () => ($('#noise-out').textContent = (+noise.value).toFixed(2)));
  $('#form-entry')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const j = await post(`/api/lot/${L.id}/entry`, fd(e.target)), out = $('#out-entry');
    if (!j.ok) { fail(out, j); return refresh(); }
    const box = show(out, 'Gate open. Ticket issued', [
      ['Ticket', j.ticket_id], ['Plate', j.plate], ['Slot', j.slot], ['Entry', j.entry_time],
      ['Plate confidence', j.confidence.toFixed(2)], ['Price rate', '\u00d7' + j.multiplier + ' (locked at entry)']]);
    box.classList.add('ticket');
    $('#form-exit [name=ticket]').value = j.ticket_id;
    toast(`Ticket ${j.ticket_id}, slot ${j.slot}`, 'success'); await refresh(); showRoute(j.slot);
  });

  $('#btn-quote')?.addEventListener('click', async () => {
    const f = $('#form-exit'), d = fd(f), out = $('#quote-box');
    const j = await post(`/api/lot/${L.id}/quote`, {ticket: d.ticket, minutes: d.minutes});
    if (!j.ok) return fail(out, j);
    show(out, `Fee due: Rs ${j.fee.toFixed(2)}`, [['Stayed', j.minutes + ' min'], ['Rate', '\u00d7' + j.multiplier], ['Slot', j.slot]]);
    f.paid.value = j.fee.toFixed(2);
  });
  $('#form-exit')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const j = await post(`/api/lot/${L.id}/exit`, fd(e.target)), out = $('#out-exit');
    if (!j.ok) return fail(out, j);
    show(out, 'Payment received. Gate open', [
      ['Ticket', j.ticket_id], ['Stayed', j.minutes + ' min'], ['Fee', 'Rs ' + j.fee.toFixed(2)],
      ['Paid by', j.method], ['Paid', 'Rs ' + j.paid.toFixed(2)], ['Change', 'Rs ' + j.change.toFixed(2)]]);
    $('#quote-box').innerHTML = ''; route = null; $('#route-card').hidden = true;
    toast('Payment received', 'success'); refresh();
  });

  refresh(); setInterval(refresh, 4000);
})();
