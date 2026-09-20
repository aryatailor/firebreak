/* Firebreak — renders web/<DATA_DIR>/ (CONTRACT.md) as the one-screen demo (DESIGN.md).
   No frameworks, no CDN; Leaflet is vendored. Switching to the real pipeline output is
   the one-line DATA_DIR flip. Append ?offline=1 to force the offline basemap path. */

const DATA_DIR = 'mock';
const UNREACHED = 65535;
const TILE_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const FORCE_OFFLINE = new URLSearchParams(location.search).has('offline');
const $ = id => document.getElementById(id);

async function loadJSON(name) {
  const r = await fetch(`${DATA_DIR}/${name}`);
  if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
  return r.json();
}

/* CONTRACT.md: base64 of little-endian uint16, row-major, row 0 = north. */
function decodeGrid(b64, rows, cols) {
  const bin = atob(b64);
  if (bin.length !== rows * cols * 2) {
    throw new Error(`grid is ${bin.length} bytes, expected ${rows * cols * 2}`);
  }
  const bytes = Uint8Array.from(bin, c => c.charCodeAt(0));
  const dv = new DataView(bytes.buffer);
  const out = new Uint16Array(rows * cols);
  for (let i = 0; i < out.length; i++) out[i] = dv.getUint16(i * 2, true);
  return out;
}

/* Fire age ramp (DESIGN.md): #ffd166 → #ff7a1a → #c1121f → #2b2b2b across RAMP_SPAN
   minutes of age, precomputed as a per-minute RGB lookup table. */
const RAMP_SPAN = 180;
const RAMP = (() => {
  const stops = [[255, 209, 102], [255, 122, 26], [193, 18, 31], [43, 43, 43]];
  const lut = new Uint8Array((RAMP_SPAN + 1) * 3);
  for (let m = 0; m <= RAMP_SPAN; m++) {
    const f = (m / RAMP_SPAN) * (stops.length - 1);
    const i = Math.min(Math.floor(f), stops.length - 2), t = f - i;
    for (let k = 0; k < 3; k++) {
      lut[m * 3 + k] = Math.round(stops[i][k] + (stops[i + 1][k] - stops[i][k]) * t);
    }
  }
  return lut;
})();

const fmtMoney = n =>       // 999500+ takes the M branch so nothing prints "$1000k"
  n >= 999500 ? `$${+(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `$${Math.round(n / 1e3)}k` : `$${n}`;
const fmtTime = m => `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;

/* Fire canvas layer: one pixel per grid cell, stretched over meta.bounds
   (image-rendering: pixelated keeps cells crisp). Positioning mirrors L.ImageOverlay.
   A second, smooth-scaled canvas carries a blurred glow on cells ignited within the
   last GLOW_SPAN minutes of the current t. */
const GLOW_SPAN = 30;
const FireLayer = L.Layer.extend({
  initialize(bounds, rows, cols, opts) {
    L.setOptions(this, opts);
    this._b = bounds; this._rows = rows; this._cols = cols;
  },
  onAdd() {
    const c = this._canvas = L.DomUtil.create('canvas', 'fire-canvas leaflet-zoom-animated');
    const g = this._glow = L.DomUtil.create('canvas', 'glow-canvas leaflet-zoom-animated');
    c.width = g.width = this._cols; c.height = g.height = this._rows;
    this._ctx = c.getContext('2d');
    this._gctx = g.getContext('2d');
    this._off = document.createElement('canvas');       // unblurred glow cells
    this._off.width = this._cols; this._off.height = this._rows;
    this._octx = this._off.getContext('2d');
    this._img = this._ctx.createImageData(this._cols, this._rows);
    this._gimg = this._octx.createImageData(this._cols, this._rows);
    this.getPane().appendChild(c);
    this.getPane().appendChild(g);                      // glow above the cells
    this._reset();
  },
  onRemove() { this._canvas.remove(); this._glow.remove(); },
  getEvents() {
    const ev = { zoom: this._reset, viewreset: this._reset };
    if (this._zoomAnimated) ev.zoomanim = this._animateZoom;
    return ev;
  },
  _reset() {
    const nw = this._map.latLngToLayerPoint(this._b.getNorthWest());
    const se = this._map.latLngToLayerPoint(this._b.getSouthEast());
    for (const el of [this._canvas, this._glow]) {
      L.DomUtil.setPosition(el, nw);
      el.style.width = `${se.x - nw.x}px`;
      el.style.height = `${se.y - nw.y}px`;
    }
  },
  _animateZoom(e) {
    const nb = this._map._latLngBoundsToNewLayerBounds(this._b, e.zoom, e.center);
    const scale = this._map.getZoomScale(e.zoom);
    L.DomUtil.setTransform(this._canvas, nb.min, scale);
    L.DomUtil.setTransform(this._glow, nb.min, scale);
  },
  draw(arrival, t) {
    const d = this._img.data, gd = this._gimg.data;
    for (let i = 0; i < arrival.length; i++) {
      const a = arrival[i], o = i * 4;
      if (a > t) { d[o + 3] = 0; gd[o + 3] = 0; continue; }  // unburned (incl. 65535)
      const age = t - a;
      const k = Math.min(age, RAMP_SPAN) * 3;
      d[o] = RAMP[k]; d[o + 1] = RAMP[k + 1]; d[o + 2] = RAMP[k + 2];
      d[o + 3] = 191;                             // 0.75 alpha (DESIGN.md)
      if (age <= GLOW_SPAN) {                     // fresh ignition: amber-white glow
        gd[o] = 255; gd[o + 1] = 226; gd[o + 2] = 150;
        gd[o + 3] = 220 - Math.round((220 * age) / GLOW_SPAN);
      } else gd[o + 3] = 0;
    }
    this._ctx.putImageData(this._img, 0, 0);
    this._octx.putImageData(this._gimg, 0, 0);
    const g = this._gctx;
    g.clearRect(0, 0, this._cols, this._rows);
    g.filter = 'blur(1.5px)';
    g.drawImage(this._off, 0, 0);
    g.filter = 'none';
  },
});

/* Building dots: a viewport-sized canvas (scales to 10k+ points where per-point DOM
   markers would not). `hits` is a Uint8Array shared with the app's render loop. */
const DotsLayer = L.Layer.extend({
  initialize(latlngs, hits, opts) {
    L.setOptions(this, opts);
    this._ll = latlngs; this._hits = hits;
    this._x = new Float32Array(latlngs.length);
    this._y = new Float32Array(latlngs.length);
  },
  onAdd() {
    this._canvas = L.DomUtil.create('canvas', 'dots-canvas leaflet-zoom-animated');
    this._ctx = this._canvas.getContext('2d');
    this.getPane().appendChild(this._canvas);
    this._reset();
  },
  onRemove() { this._canvas.remove(); },
  getEvents() {
    // 'zoom' is required: pinch-zoom fires per-frame 'zoom' with no zoomanim.
    const ev = { zoom: this._reset, moveend: this._reset, viewreset: this._reset, resize: this._reset };
    if (this._zoomAnimated) ev.zoomanim = this._animateZoom;
    return ev;
  },
  _reset() {
    const tl = this._map.containerPointToLayerPoint([0, 0]);
    const s = this._map.getSize();
    L.DomUtil.setPosition(this._canvas, tl);
    if (this._canvas.width !== s.x || this._canvas.height !== s.y) {
      this._canvas.width = s.x; this._canvas.height = s.y;
    }
    for (let i = 0; i < this._ll.length; i++) {
      const p = this._map.latLngToContainerPoint(this._ll[i]);
      this._x[i] = p.x; this._y[i] = p.y;
    }
    this.redraw();
  },
  _animateZoom(e) {
    const scale = this._map.getZoomScale(e.zoom);
    const off = this._map._getCenterOffset(e.center)._multiplyBy(-scale)
      .subtract(this._map._getMapPanePos());
    L.DomUtil.setTransform(this._canvas, off, scale);
  },
  redraw() {
    const { width: w, height: h } = this._canvas;
    const ctx = this._ctx, x = this._x, y = this._y, hits = this._hits;
    ctx.clearRect(0, 0, w, h);
    for (let pass = 0; pass < 2; pass++) {         // unhit below, hit on top
      ctx.fillStyle = pass ? '#ff3b3b' : 'rgba(255,255,255,0.4)';
      for (let i = 0; i < x.length; i++) {
        if (hits[i] !== pass) continue;
        if (x[i] < -3 || y[i] < -3 || x[i] > w + 3 || y[i] > h + 3) continue;
        ctx.fillRect(x[i] - 1.5, y[i] - 1.5, 3, 3);  // 3 px dots (DESIGN.md)
      }
    }
  },
});

/* Basemap: satellite tiles with the hillshade/fuel PNG blended over at 35% for
   relief. The page renders offline-first — the PNG starts at full opacity, so
   nothing waits on the network — and steps back to 35% only after a probe tile AND
   a full tile-layer load succeed (usually < 3 s; a slow first handshake just
   upgrades late). A failed probe means no tiles ever; any tileerror returns the
   PNG to full strength until tiles complete a clean load again. */
const BLEND_OPACITY = 0.35;
function setupBasemap(map, bounds, onMode) {
  const png = L.imageOverlay(`${DATA_DIR}/basemap.png`, bounds, { opacity: 1 }).addTo(map);
  onMode('offline basemap');
  if (FORCE_OFFLINE) { onMode('offline basemap (forced)'); return; }
  const probe = new Image();
  probe.onload = () => {
    const tiles = L.tileLayer(TILE_URL, { maxZoom: 17, attribution: 'Imagery © Esri' }).addTo(map);
    // Leaflet fires 'load' when a batch settles even if every tile errored, so a
    // clean-batch flag is needed or 'load' would undo the tileerror fallback.
    let errored = false;
    tiles.on('loading', () => { errored = false; });
    tiles.on('tileerror', () => { errored = true; png.setOpacity(1); onMode('offline basemap (tiles failed)'); });
    tiles.on('load', () => {
      if (!errored) { png.setOpacity(BLEND_OPACITY); onMode('satellite + hillshade (online)'); }
    });
  };
  probe.src = TILE_URL.replace('{z}', 0).replace('{y}', 0).replace('{x}', 0) + `?probe=${Date.now()}`;
}

/* Inline SVG: step curve of cumulative cost vs cumulative houses saved (curve.json),
   with the current budget position marked. Returns {mark} to move the marker. */
function buildCurve(points) {
  const W = 308, H = 150, ML = 34, MR = 8, MT = 8, MB = 18;
  const pts = [{ cumulative_cost: 0, cumulative_saved: 0 }, ...points];
  const last = pts[pts.length - 1];
  const xmax = Math.max(last.cumulative_cost, 1), ymax = Math.max(last.cumulative_saved, 1);
  const X = c => ML + (c / xmax) * (W - ML - MR);
  const Y = s => H - MB - (s / ymax) * (H - MB - MT);
  let d = `M${X(0)} ${Y(0)}`;
  for (const p of pts.slice(1)) {
    d += `H${X(p.cumulative_cost).toFixed(1)}V${Y(p.cumulative_saved).toFixed(1)}`;
  }
  const svg = $('curve');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.innerHTML =
    `<line class="grid" x1="${ML}" y1="${Y(ymax)}" x2="${W - MR}" y2="${Y(ymax)}"/>` +
    `<line class="grid" x1="${ML}" y1="${Y(ymax / 2)}" x2="${W - MR}" y2="${Y(ymax / 2)}"/>` +
    `<line class="axis" x1="${ML}" y1="${Y(0)}" x2="${W - MR}" y2="${Y(0)}"/>` +
    `<line class="axis" x1="${ML}" y1="${Y(0)}" x2="${ML}" y2="${MT}"/>` +
    `<text class="lbl" x="${ML - 4}" y="${Y(0) + 3}" text-anchor="end">0</text>` +
    `<text class="lbl" x="${ML - 4}" y="${Y(ymax / 2) + 3}" text-anchor="end">${Math.round(ymax / 2)}</text>` +
    `<text class="lbl" x="${ML - 4}" y="${Y(ymax) + 3}" text-anchor="end">${ymax}</text>` +
    `<text class="lbl" x="${ML}" y="${H - 4}">$0</text>` +
    `<text class="lbl" x="${W - MR}" y="${H - 4}" text-anchor="end">${fmtMoney(xmax)}</text>` +
    `<path class="curve-line" d="${d}"/>` +
    `<line id="curve-mark" class="mark" x1="0" x2="0" y1="${MT}" y2="${Y(0)}"/>` +
    `<circle id="curve-dot" r="4"/>`;
  let selText = '';
  const near = c => pts.reduce((b, p) =>
    Math.abs(p.cumulative_cost - c) < Math.abs(b.cumulative_cost - c) ? p : b);
  svg.onmousemove = e => {
    const r = svg.getBoundingClientRect();
    const cost = ((e.clientX - r.left) * (W / r.width) - ML) / (W - ML - MR) * xmax;
    const p = near(cost);
    $('curve-readout').textContent = `${fmtMoney(p.cumulative_cost)} → ${p.cumulative_saved} saved`;
  };
  svg.onmouseleave = () => { $('curve-readout').textContent = selText; };
  return {
    mark(cost, saved, label) {
      $('curve-mark').setAttribute('x1', X(cost));
      $('curve-mark').setAttribute('x2', X(cost));
      $('curve-dot').setAttribute('cx', X(cost));
      $('curve-dot').setAttribute('cy', Y(saved));
      selText = label;
      $('curve-readout').textContent = label;
    },
  };
}

async function main() {
  const meta = await loadJSON('meta.json');
  const [baseline, solutions, curve, buildingsFC, legend] = await Promise.all([
    loadJSON('baseline.json'), loadJSON('solutions.json'), loadJSON('curve.json'),
    loadJSON('buildings.geojson'), loadJSON('fuel_legend.json'),
  ]);
  const { rows, cols } = meta.grid, b = meta.bounds;
  const bounds = L.latLngBounds([b.south, b.west], [b.north, b.east]);

  $('story').textContent = meta.story;
  $('data-tag').textContent = `data: web/${DATA_DIR}/`;
  $('legend').innerHTML = legend.map(g =>
    `<span class="chip"><i style="background:${g.color}"></i>${g.group}</span>`).join('');
  $('simp-list').innerHTML = meta.simplifications.map(s => `<li>${s}</li>`).join('');

  const map = L.map('map', { zoomSnap: 0.25, maxZoom: 17 });
  map.fitBounds(bounds, { padding: [10, 10] });
  ['fire', 'dots', 'vec'].forEach((n, i) => { map.createPane(n).style.zIndex = 405 + i; });

  setupBasemap(map, bounds, mode => {
    $('basemap-status').textContent = mode;
    $('legend').hidden = !mode.startsWith('offline');
  });

  // Buildings → flat arrays; `hits` is shared with DotsLayer.
  const feats = buildingsFC.features, nB = feats.length;
  const cells = new Uint32Array(nB), hits = new Uint8Array(nB);
  const lls = feats.map((f, i) => {
    cells[i] = f.properties.row * cols + f.properties.col;
    return L.latLng(f.geometry.coordinates[1], f.geometry.coordinates[0]);
  });
  $('stat-hit-label').textContent = `buildings hit of ${nB.toLocaleString()}`;

  const fire = new FireLayer(bounds, rows, cols, { pane: 'fire' }).addTo(map);
  const dots = new DotsLayer(lls, hits, { pane: 'dots' }).addTo(map);
  const vecRenderer = L.svg({ pane: 'vec' });
  L.circleMarker([meta.ignition.lat, meta.ignition.lon], {
    renderer: vecRenderer, radius: 5, color: '#ffd166', weight: 2,
    fillColor: '#ff7a1a', fillOpacity: 0.9,
  }).addTo(map).bindTooltip(`Ignition — ${meta.ignition.label}`);

  const grids = [decodeGrid(baseline.arrival_min_b64, rows, cols)];  // [0]=baseline
  const gridFor = i =>
    grids[i] || (grids[i] = decodeGrid(solutions[i - 1].arrival_min_b64, rows, cols));

  const chart = buildCurve(curve.points);
  const state = { t: 0, arrival: grids[0] };
  let breaksLayer = null;

  function render() {
    const { t, arrival } = state, base = grids[0];
    let hit = 0, baseHit = 0;
    for (let i = 0; i < nB; i++) {
      const h = arrival[cells[i]] <= t ? 1 : 0;
      hits[i] = h; hit += h;
      if (base[cells[i]] <= t) baseHit++;
    }
    fire.draw(arrival, t);
    dots.redraw();
    $('stat-hit').textContent = hit.toLocaleString();
    $('stat-saved').textContent = Math.max(0, baseHit - hit).toLocaleString();
    $('time-label').textContent = fmtTime(t);
  }

  function setBudget(idx) {
    const sol = idx > 0 ? solutions[idx - 1] || null : null;
    state.arrival = sol ? gridFor(idx) : grids[0];
    if (breaksLayer) { map.removeLayer(breaksLayer); breaksLayer = null; }
    if (sol) {
      breaksLayer = L.geoJSON(sol.breaks, {
        renderer: vecRenderer,
        style: { color: '#ffffff', weight: 1, fillColor: '#3ddc84', fillOpacity: 0.8 },
      }).addTo(map);
    }
    $('budget-label').textContent = sol ? `${fmtMoney(sol.budget)} budget` : '$0 — baseline';
    $('stat-spent').textContent = sol ? fmtMoney(sol.cost) : '$0';
    $('stat-spent').title = sol ? `$${sol.cost.toLocaleString()} spent of $${sol.budget.toLocaleString()}` : '';
    chart.mark(sol ? sol.cost : 0, sol ? sol.stats.houses_saved : 0,
      sol ? `${fmtMoney(sol.cost)} → ${sol.stats.houses_saved} saved` : '$0 — baseline');
    render();
  }

  const budgetEl = $('budget'), timeEl = $('time'), playEl = $('play');
  budgetEl.max = String(meta.budgets.length);   // 0 = baseline, i = budgets[i-1]
  $('budget-ticks').innerHTML =
    ['$0', ...meta.budgets.map(fmtMoney)].map(s => `<span>${s}</span>`).join('');
  budgetEl.oninput = () => setBudget(Number(budgetEl.value));

  timeEl.max = String(meta.horizon_min);
  timeEl.oninput = () => { state.t = Number(timeEl.value); render(); };

  const TICK_MS = 20000 / (meta.horizon_min / 5);   // full sweep ≈ 20 s (DESIGN.md)
  let timer = null;
  function stopPlay() {
    clearInterval(timer); timer = null;
    playEl.innerHTML = '&#9654;&#xFE0E;'; playEl.setAttribute('aria-label', 'Play');
  }
  function startPlay() {
    if (timer) return;
    if (state.t >= meta.horizon_min) { state.t = 0; timeEl.value = '0'; render(); }
    playEl.innerHTML = '&#10074;&#10074;'; playEl.setAttribute('aria-label', 'Pause');
    timer = setInterval(() => {
      state.t = Math.min(state.t + 5, meta.horizon_min);
      timeEl.value = String(state.t);
      render();
      if (state.t >= meta.horizon_min) stopPlay();
    }, TICK_MS);
  }
  playEl.onclick = () => (timer ? stopPlay() : startPlay());
  document.addEventListener('keydown', e => {
    if (e.code === 'Space' && !/^(BUTTON|INPUT|SELECT|TEXTAREA|SUMMARY)$/.test(e.target.tagName)) {
      e.preventDefault(); playEl.click();
    }
  });

  setBudget(0);
  startPlay();          // auto-run once so the demo lands immediately
}

main().catch(err => {
  $('story').textContent = `FAILED to load web/${DATA_DIR}/ — ${err.message}`;
  console.error(err);
});
