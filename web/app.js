/* Firebreak — renders web/<DATA_DIR>/ (CONTRACT.md) as the one-screen demo.
   No frameworks, no CDN; Leaflet is vendored. Switching to the real pipeline output
   is the one-line DATA_DIR flip. URL flags: ?offline=1 forces the offline basemap,
   ?intro=0 skips the title card (testing). */

const DATA_DIR = 'mock';
const UNREACHED = 65535;
const TILE_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const PARAMS = new URLSearchParams(location.search);
const FORCE_OFFLINE = PARAMS.has('offline');
const SKIP_INTRO = PARAMS.get('intro') === '0';
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

/* steps.json arrays: uint8, arrival bucketed to bucket_min minutes, `never` = not
   reached within the horizon. Expanded to uint16 minutes so the fire pipeline is
   uniform. */
function decodeStepGrid(b64, rows, cols, bucketMin, never) {
  const bin = atob(b64);
  if (bin.length !== rows * cols) {
    throw new Error(`step grid is ${bin.length} bytes, expected ${rows * cols}`);
  }
  const out = new Uint16Array(bin.length);
  for (let i = 0; i < bin.length; i++) {
    const v = bin.charCodeAt(i);
    out[i] = v === never ? UNREACHED : v * bucketMin;
  }
  return out;
}

/* Fire age ramp: #ffd166 → #ff7a1a → #c1121f → translucent charcoal #1a1a1a across
   RAMP_SPAN minutes of age, precomputed as a per-minute RGB lookup table. */
const RAMP_SPAN = 180;
const RAMP = (() => {
  const stops = [[255, 209, 102], [255, 122, 26], [193, 18, 31], [26, 26, 26]];
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
const fmtM2 = n =>          // 2-decimal variant for the continuous budget readout
  n >= 999995 ? `$${(n / 1e6).toFixed(2).replace(/\.?0+$/, '')}M`
    : n >= 1e3 ? `$${Math.round(n / 1e3)}k` : `$${n}`;
const fmtTime = m => `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
const merc = lat => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));

/* Fire canvas layer: 2 px per grid cell (blurred via CSS so cells read soft),
   stretched over meta.bounds; positioning mirrors L.ImageOverlay. A second,
   smooth-scaled canvas carries a blurred glow on cells ignited within the last
   GLOW_SPAN minutes. Break cells render as cleared ground until fire crosses them.
   draw() returns the count of fresh-front cells (feeds the audio level). */
const GLOW_SPAN = 30;
const FireLayer = L.Layer.extend({
  initialize(bounds, rows, cols, opts) {
    L.setOptions(this, opts);
    this._b = bounds; this._rows = rows; this._cols = cols;
    this._mask = null;          // Uint8Array: 0 none, 1 break, 2 break edge
    this._hover = -1;           // hovered break index (cells highlight green)
    this._cellBreak = null;     // Int16Array cell → break index (-1 none)
  },
  onAdd() {
    const c = this._canvas = L.DomUtil.create('canvas', 'fire-canvas leaflet-zoom-animated');
    const g = this._glow = L.DomUtil.create('canvas', 'glow-canvas leaflet-zoom-animated');
    c.width = this._cols * 2; c.height = this._rows * 2;
    g.width = this._cols; g.height = this._rows;
    this._ctx = c.getContext('2d');
    this._gctx = g.getContext('2d');
    this._off = document.createElement('canvas');
    this._off.width = this._cols; this._off.height = this._rows;
    this._octx = this._off.getContext('2d');
    this._img = this._ctx.createImageData(this._cols * 2, this._rows * 2);
    this._gimg = this._octx.createImageData(this._cols, this._rows);
    this._frame = 0;
    this.getPane().appendChild(c);
    this.getPane().appendChild(g);
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
  setBreaks(mask, cellBreak) { this._mask = mask; this._cellBreak = cellBreak; },
  setHover(bi) { this._hover = bi; },
  draw(arrival, t) {
    const frame = this._frame = (this._frame + 1) & 1023;
    const cols = this._cols, W2 = cols * 2, row4 = W2 * 4;
    const d = this._img.data, gd = this._gimg.data;
    const mask = this._mask, cb = this._cellBreak, hover = this._hover;
    let fresh = 0;
    for (let i = 0; i < arrival.length; i++) {
      const a = arrival[i], go = i * 4;
      const o = (((i / cols) | 0) * 2 * W2 + (i % cols) * 2) * 4;   // 2×2 block
      let R = 0, G = 0, B = 0, A = 0;
      if (a <= t) {
        const age = t - a;
        const k = Math.min(age, RAMP_SPAN) * 3;
        R = RAMP[k]; G = RAMP[k + 1]; B = RAMP[k + 2];
        if (age <= 15) {
          fresh++;
          // feathered fresh perimeter at 0.55, flickering ±0.1 (seeded, per frame)
          A = 140 + ((((i * 2654435761 ^ frame * 40503) >>> 0) & 255) - 128) * 0.2;
          A = A < 0 ? 0 : A;
        } else if (age >= RAMP_SPAN) A = 179;                    // charcoal at 0.70
        else if (age >= 120) A = 217 - ((age - 120) * 38) / 60;  // 0.85 → 0.70
        else A = 217;                                            // body at 0.85
        if (age <= GLOW_SPAN) {                   // fresh ignition: amber-white glow
          gd[go] = 255; gd[go + 1] = 226; gd[go + 2] = 150;
          gd[go + 3] = 220 - Math.round((220 * age) / GLOW_SPAN);
        } else gd[go + 3] = 0;
      } else {
        gd[go + 3] = 0;
        if (mask && mask[i]) {                    // cleared ground (fuel break)
          if (hover >= 0 && cb[i] === hover) { R = 61; G = 220; B = 132; A = 217; }
          else if (mask[i] === 2) { R = 233; G = 220; B = 188; A = 89; }   // edge .35
          else { R = 217; G = 201; B = 163; A = 217; }                     // #d9c9a3 .85
        }
      }
      d[o] = R; d[o + 1] = G; d[o + 2] = B; d[o + 3] = A;
      d[o + 4] = R; d[o + 5] = G; d[o + 6] = B; d[o + 7] = A;
      d[o + row4] = R; d[o + row4 + 1] = G; d[o + row4 + 2] = B; d[o + row4 + 3] = A;
      d[o + row4 + 4] = R; d[o + row4 + 5] = G; d[o + row4 + 6] = B; d[o + row4 + 7] = A;
    }
    this._ctx.putImageData(this._img, 0, 0);
    this._octx.putImageData(this._gimg, 0, 0);
    const g = this._gctx;
    g.clearRect(0, 0, this._cols, this._rows);
    g.filter = 'blur(1.5px)';
    g.drawImage(this._off, 0, 0);
    g.filter = 'none';
    return fresh;
  },
});

/* Homes layer: a canvas anchored to meta.bounds through the SAME positioning path
   as the fire canvas, so homes can never drift against the grid. The backing store
   is resized to match screen resolution on zoomend (capped), and homes are drawn at
   their true sub-cell position (mercator-correct fx/fy fractions of the bounds).
   `states` is shared with the render loop: 0 standing, 1 burned, 2 saved. */
const HousesLayer = L.Layer.extend({
  initialize(bounds, fx, fy, states, opts) {
    L.setOptions(this, opts);
    this._b = bounds; this._fx = fx; this._fy = fy; this._st = states;
    this._sprites = {};
  },
  onAdd() {
    this._canvas = L.DomUtil.create('canvas', 'houses-canvas leaflet-zoom-animated');
    this._ctx = this._canvas.getContext('2d');
    this.getPane().appendChild(this._canvas);
    this._resize();
  },
  onRemove() { this._canvas.remove(); },
  getEvents() {
    const ev = { zoom: this._reset, viewreset: this._resize, zoomend: this._resize };
    if (this._zoomAnimated) ev.zoomanim = this._animateZoom;
    return ev;
  },
  _place() {
    const nw = this._map.latLngToLayerPoint(this._b.getNorthWest());
    const se = this._map.latLngToLayerPoint(this._b.getSouthEast());
    L.DomUtil.setPosition(this._canvas, nw);
    this._canvas.style.width = `${se.x - nw.x}px`;
    this._canvas.style.height = `${se.y - nw.y}px`;
    return [se.x - nw.x, se.y - nw.y];
  },
  _reset() { this._place(); },
  _resize() {
    const [w, h] = this._place();
    const W = Math.max(64, Math.min(4096, Math.round(w)));
    const H = Math.max(64, Math.round(W * (h / Math.max(w, 1))));
    if (this._canvas.width !== W || this._canvas.height !== H) {
      this._canvas.width = W; this._canvas.height = H;
    }
    this._scale = w / W;   // CSS stretch past the 4096 cap; sprites compensate
    this.redraw();
  },
  _animateZoom(e) {
    const nb = this._map._latLngBoundsToNewLayerBounds(this._b, e.zoom, e.center);
    L.DomUtil.setTransform(this._canvas, nb.min, this._map.getZoomScale(e.zoom));
  },
  _sprite(icon, kind) {   // kind: 0 standing, 1 burned, 2 saved (green ring)
    const key = `${icon}-${kind}`;
    if (this._sprites[key]) return this._sprites[key];
    const s = icon ? 12 : 8;
    const cv = document.createElement('canvas');
    cv.width = s; cv.height = s;
    const c = cv.getContext('2d');
    const fill = kind === 1 ? '#ff3b3b' : 'rgba(216,216,211,0.8)';
    if (icon) {           // 6 px house pictogram: roof triangle + square body
      const p = new Path2D();
      p.moveTo(6, 2.2); p.lineTo(2.6, 5.6); p.lineTo(3.4, 5.6); p.lineTo(3.4, 9.4);
      p.lineTo(8.6, 9.4); p.lineTo(8.6, 5.6); p.lineTo(9.4, 5.6); p.closePath();
      c.strokeStyle = '#0a0b0d'; c.lineWidth = 2; c.stroke(p);   // 1 px dark edge
      c.fillStyle = fill; c.fill(p);
      if (kind === 2) { c.strokeStyle = '#3ddc84'; c.lineWidth = 1; c.stroke(p); }
    } else {              // 2×2 square with a 1 px dark edge
      c.fillStyle = '#0a0b0d'; c.fillRect(2, 2, 4, 4);
      c.fillStyle = fill; c.fillRect(3, 3, 2, 2);
      if (kind === 2) { c.strokeStyle = '#3ddc84'; c.strokeRect(1.5, 1.5, 5, 5); }
    }
    return (this._sprites[key] = cv);
  },
  redraw() {
    const cv = this._canvas, ctx = this._ctx, w = cv.width, h = cv.height;
    const icon = this._map.getZoom() >= 14;
    const sc = this._scale || 1;
    const size = (icon ? 12 : 8) / sc, half = size / 2;   // constant on-screen size
    const spr = [this._sprite(icon, 0), this._sprite(icon, 1), this._sprite(icon, 2)];
    const fx = this._fx, fy = this._fy, st = this._st;
    ctx.clearRect(0, 0, w, h);
    if (sc === 1) {
      for (let i = 0; i < fx.length; i++) {
        ctx.drawImage(spr[st[i]], Math.round(fx[i] * w) - half, Math.round(fy[i] * h) - half);
      }
    } else {
      for (let i = 0; i < fx.length; i++) {
        ctx.drawImage(spr[st[i]], fx[i] * w - half, fy[i] * h - half, size, size);
      }
    }
  },
});

/* Offline basemap PNG with its edges feathered by a `feather`-px alpha ramp so it
   fades into the dark page instead of reading as a pasted rectangle. */
function featheredOverlay(src, bounds, feather) {
  return new Promise(resolve => {
    const img = new Image();
    img.onerror = () => resolve(L.imageOverlay(src, bounds));   // serve it raw
    img.onload = () => {
      const cv = document.createElement('canvas');
      const w = cv.width = img.width, h = cv.height = img.height;
      const x = cv.getContext('2d');
      x.drawImage(img, 0, 0);
      x.globalCompositeOperation = 'destination-out';
      const f = Math.min(feather, w / 4, h / 4);
      const edges = [
        [0, 0, f, 0, 0, 0, f, h], [w, 0, w - f, 0, w - f, 0, f, h],
        [0, 0, 0, f, 0, 0, w, f], [0, h, 0, h - f, 0, h - f, w, f],
      ];
      for (const [gx0, gy0, gx1, gy1, rx, ry, rw, rh] of edges) {
        const grad = x.createLinearGradient(gx0, gy0, gx1, gy1);
        grad.addColorStop(0, 'rgba(0,0,0,1)');
        grad.addColorStop(1, 'rgba(0,0,0,0)');
        x.fillStyle = grad;
        x.fillRect(rx, ry, rw, rh);
      }
      resolve(L.imageOverlay(cv.toDataURL(), bounds));
    };
    img.src = src;
  });
}

/* Basemap: dimmed satellite tiles are the base; the feathered PNG is ONLY the
   offline fallback, never blended over satellite. Offline-first: the PNG shows
   until a clean tile batch lands; any tileerror brings it straight back. */
async function setupBasemap(map, bounds, onMode) {
  const png = await featheredOverlay(`${DATA_DIR}/basemap.png`, bounds, 40);
  png.addTo(map);
  onMode('OFFLINE');
  if (FORCE_OFFLINE) { onMode('OFFLINE (FORCED)'); return; }
  const probe = new Image();
  probe.onload = () => {
    const tiles = L.tileLayer(TILE_URL, { maxZoom: 17, attribution: 'Imagery © Esri' }).addTo(map);
    // Leaflet fires 'load' when a batch settles even if every tile errored, so a
    // clean-batch flag is needed or 'load' would undo the tileerror fallback.
    let errored = false;
    tiles.on('loading', () => { errored = false; });
    tiles.on('tileerror', () => {
      errored = true;
      if (!map.hasLayer(png)) png.addTo(map);
      onMode('OFFLINE (TILES FAILED)');
    });
    tiles.on('load', () => {
      if (!errored) {
        if (map.hasLayer(png)) map.removeLayer(png);
        onMode('SATELLITE');
      }
    });
  };
  probe.src = TILE_URL.replace('{z}', 0).replace('{y}', 0).replace('{x}', 0) + `?probe=${Date.now()}`;
}

/* Breaks → grid cells. Polygons are lat/lon; rows are uniform in mercator-y between
   the bounds, so lat converts through merc(). Returns per-break cell lists (+ edge
   flags baked later) and a cell → break-index map for hover. */
function rasterizeBreaks(breaksFC, meta) {
  const { rows, cols } = meta.grid, b = meta.bounds;
  const yN = merc(b.north), yS = merc(b.south);
  const latToRow = lat => ((yN - merc(lat)) / (yN - yS)) * rows;
  const lonToCol = lon => ((lon - b.west) / (b.east - b.west)) * cols;
  const cellBreak = new Int16Array(rows * cols).fill(-1);
  const pip = (x, y, ring) => {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  };
  const breaks = breaksFC.features.map((ft, bi) => {
    const ring = ft.geometry.coordinates[0].map(([lon, lat]) => [lonToCol(lon), latToRow(lat)]);
    let c0 = Infinity, c1 = -Infinity, r0 = Infinity, r1 = -Infinity;
    for (const [x, y] of ring) {
      c0 = Math.min(c0, x); c1 = Math.max(c1, x);
      r0 = Math.min(r0, y); r1 = Math.max(r1, y);
    }
    const cells = [];
    for (let r = Math.max(0, Math.floor(r0)); r <= Math.min(rows - 1, Math.ceil(r1)); r++) {
      for (let c = Math.max(0, Math.floor(c0)); c <= Math.min(cols - 1, Math.ceil(c1)); c++) {
        if (pip(c + 0.5, r + 0.5, ring)) {
          cells.push(r * cols + c);
          cellBreak[r * cols + c] = bi;
        }
      }
    }
    return { props: ft.properties, step: ft.properties.step ?? bi, cells };
  });
  return { breaks, cellBreak };
}

/* Fire crackle, synthesized — filtered brown-noise bed + random short bandpassed
   impulses. Level tracks the active front size. Must never throw. */
function makeAudio() {
  let ctx = null, master = null, muted = false, level = 0;
  function start() {
    if (ctx) return;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      ctx = new AC();
      if (ctx.state === 'suspended') ctx.resume();
      master = ctx.createGain(); master.gain.value = 0;
      master.connect(ctx.destination);
      const len = ctx.sampleRate * 4;
      const buf = ctx.createBuffer(1, len, ctx.sampleRate);
      const d = buf.getChannelData(0);
      let last = 0;
      for (let i = 0; i < len; i++) {
        last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02;
        d[i] = last * 3.5;
      }
      const src = ctx.createBufferSource(); src.buffer = buf; src.loop = true;
      const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 420;
      const bed = ctx.createGain(); bed.gain.value = 0.7;
      src.connect(lp); lp.connect(bed); bed.connect(master);
      src.start();
      setInterval(() => {
        try {
          if (!ctx || muted || level < 0.02) return;
          const n = 1 + Math.floor(Math.random() * 3 * level + 2 * level);
          for (let k = 0; k < n; k++) crackle();
        } catch (e) { /* audio must never break the page */ }
      }, 90);
    } catch (e) { ctx = null; }
  }
  function crackle() {
    const dur = 0.02 + Math.random() * 0.05;
    const len = Math.max(8, (ctx.sampleRate * dur) | 0);
    const b = ctx.createBuffer(1, len, ctx.sampleRate);
    const ch = b.getChannelData(0);
    for (let i = 0; i < len; i++) ch[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const s = ctx.createBufferSource(); s.buffer = b;
    const bp = ctx.createBiquadFilter(); bp.type = 'bandpass';
    bp.frequency.value = 900 + Math.random() * 3200; bp.Q.value = 1 + Math.random() * 4;
    const g = ctx.createGain();
    g.gain.setValueAtTime((0.15 + Math.random() * 0.5) * level, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + dur);
    s.connect(bp); bp.connect(g); g.connect(master);
    s.start(); s.stop(ctx.currentTime + dur + 0.02);
  }
  function setLevel(x) {
    level = x;
    if (!ctx || muted) return;
    try {
      master.gain.cancelScheduledValues(ctx.currentTime);
      master.gain.linearRampToValueAtTime(Math.min(0.5, x * 0.5), ctx.currentTime + 0.15);
    } catch (e) { /* ignore */ }
  }
  function toggleMute() {
    muted = !muted;
    if (!ctx) return muted;
    try { master.gain.value = muted ? 0 : Math.min(0.5, level * 0.5); } catch (e) { /* ignore */ }
    return muted;
  }
  return { start, setLevel, toggleMute, isMuted: () => muted, isActive: () => !!ctx };
}

/* Inline SVG: step curve of cumulative cost vs cumulative homes saved (curve.json),
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
    $('curve-sentence').textContent =
      `${fmtMoney(p.cumulative_cost)} saves ${p.cumulative_saved} homes`;
  };
  svg.onmouseleave = () => { $('curve-sentence').textContent = selText; };
  return {
    mark(cost, saved, label) {
      $('curve-mark').setAttribute('x1', X(cost));
      $('curve-mark').setAttribute('x2', X(cost));
      $('curve-dot').setAttribute('cx', X(cost));
      $('curve-dot').setAttribute('cy', Y(saved));
      selText = label;
      $('curve-sentence').textContent = label;
    },
  };
}

/* Waffle: the primary panel visual — one square = `unit` homes. Red fills from the
   top-left as homes burn; green-ringed squares fill from the bottom-right as the
   baseline front passes homes the breaks protect. */
function buildWaffle(total) {
  const cv = $('waffle'), COLS = 20, CELL = 14, SQ = 11;
  const unit = [1, 2, 5, 10, 20, 25, 50, 100, 200, 500].find(u => total / u <= 320) || 1000;
  const n = Math.ceil(total / unit), rows = Math.ceil(n / COLS);
  const dpr = window.devicePixelRatio || 1;
  cv.width = COLS * CELL * dpr; cv.height = rows * CELL * dpr;
  cv.style.width = `${COLS * CELL}px`; cv.style.height = `${rows * CELL}px`;
  const ctx = cv.getContext('2d');
  ctx.scale(dpr, dpr);
  $('waffle-caption').textContent = `1 SQ = ${unit} ${unit === 1 ? 'HOME' : 'HOMES'}`;
  return {
    draw(hitCount, savedCount) {
      const red = Math.min(n, Math.round(hitCount / unit));
      const green = Math.min(n - red, Math.round(savedCount / unit));
      ctx.clearRect(0, 0, COLS * CELL, rows * CELL);
      for (let i = 0; i < n; i++) {
        const cx = (i % COLS) * CELL + 1, cy = ((i / COLS) | 0) * CELL + 1;
        if (i < red) {
          ctx.fillStyle = '#ff3b3b'; ctx.fillRect(cx, cy, SQ, SQ);
        } else if (i >= n - green) {
          ctx.fillStyle = 'rgba(154,159,166,0.5)'; ctx.fillRect(cx, cy, SQ, SQ);
          ctx.strokeStyle = '#3ddc84'; ctx.lineWidth = 1;
          ctx.strokeRect(cx + 0.5, cy + 0.5, SQ - 1, SQ - 1);
        } else {
          ctx.fillStyle = 'rgba(154,159,166,0.28)'; ctx.fillRect(cx, cy, SQ, SQ);
        }
      }
    },
  };
}

async function main() {
  const meta = await loadJSON('meta.json');
  const [baseline, steps, curve, buildingsFC, breaksFC, legend] = await Promise.all([
    loadJSON('baseline.json'), loadJSON('steps.json'), loadJSON('curve.json'),
    loadJSON('buildings.geojson'), loadJSON('breaks.geojson'), loadJSON('fuel_legend.json'),
  ]);
  const { rows, cols } = meta.grid, b = meta.bounds;
  const bounds = L.latLngBounds([b.south, b.west], [b.north, b.east]);
  const H = meta.horizon_min;
  const maxBudget = meta.budgets[meta.budgets.length - 1];

  $('story').textContent = meta.story;
  $('intro-story').textContent = meta.story;
  $('legend').innerHTML = legend.map(g =>
    `<span class="chip"><i style="background:${g.color}"></i>${g.group}</span>`).join('');
  $('simp-list').innerHTML = meta.simplifications.map(s => `<li>${s}</li>`).join('');

  const map = L.map('map', { zoomSnap: 0.25, maxZoom: 17 });
  map.fitBounds(bounds, { padding: [10, 10] });
  ['fire', 'houses'].forEach((n, i) => { map.createPane(n).style.zIndex = 405 + i; });

  const windDir = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(meta.wind.from_deg / 45) % 8];
  const statusBits = mode => [
    mode, `DATA: ${DATA_DIR.toUpperCase()}`, `${meta.grid.cell_m} M CELLS`,
    `WIND ${windDir} ${meta.wind.speed_mph} MPH`,
  ].join(' · ');
  setupBasemap(map, bounds, mode => {
    $('basemap-status').textContent = statusBits(mode);
    $('legend').hidden = !mode.startsWith('OFFLINE');
  });

  // Buildings → flat arrays; `states` is shared with HousesLayer (0/1/2).
  const feats = buildingsFC.features, nB = feats.length;
  const cells = new Uint32Array(nB), states = new Uint8Array(nB);
  const fx = new Float32Array(nB), fy = new Float32Array(nB);
  const yN = merc(b.north), yS = merc(b.south);
  feats.forEach((f, i) => {
    cells[i] = f.properties.row * cols + f.properties.col;
    const [lon, lat] = f.geometry.coordinates;
    fx[i] = (lon - b.west) / (b.east - b.west);
    fy[i] = (yN - merc(lat)) / (yN - yS);
  });
  $('stat-hit-label').textContent = `homes hit of ${nB.toLocaleString()}`;

  const fire = new FireLayer(bounds, rows, cols, { pane: 'fire' }).addTo(map);
  const houses = new HousesLayer(bounds, fx, fy, states, { pane: 'houses' }).addTo(map);
  // 6:30 AM is the Camp Fire's ignition time — hard-coded until meta grows a field.
  L.marker([meta.ignition.lat, meta.ignition.lon], {
    interactive: false, keyboard: false,
    icon: L.divIcon({
      className: 'ign', iconSize: [0, 0],
      html: `<span class="ign-dot"></span><span class="ign-label">${meta.ignition.label.split(' (')[0]} · 6:30 AM</span>`,
    }),
  }).addTo(map);

  // Breaks → cells; mask marks active break cells (1) and their edges (2).
  const braster = rasterizeBreaks(breaksFC, meta);
  const breakMask = new Uint8Array(rows * cols);
  fire.setBreaks(breakMask, braster.cellBreak);
  function rebuildBreakMask(stepIdx) {
    breakMask.fill(0);
    for (const bk of braster.breaks) {
      if (bk.step <= stepIdx) for (const c of bk.cells) breakMask[c] = 1;
    }
    for (const bk of braster.breaks) {
      if (bk.step > stepIdx) continue;
      for (const c of bk.cells) {
        const r = (c / cols) | 0, cc = c % cols;
        if (r === 0 || r === rows - 1 || cc === 0 || cc === cols - 1 ||
            !breakMask[c - cols] || !breakMask[c + cols] ||
            !breakMask[c - 1] || !breakMask[c + 1]) breakMask[c] = 2;
      }
    }
  }

  // Grids: baseline is uint16 minutes; steps are uint8 buckets, expanded on demand.
  const baseGrid = decodeGrid(baseline.arrival_min_b64, rows, cols);
  const bucketMin = steps.bucket_min || 5, never8 = steps.never == null ? 255 : steps.never;
  const stepGrids = [];
  const gridForStep = s => s < 0 ? baseGrid :
    stepGrids[s] || (stepGrids[s] = decodeStepGrid(steps.steps[s].arrival_5min_b64, rows, cols, bucketMin, never8));
  const stepForBudget = v => {
    let s = -1;
    for (let i = 0; i < steps.steps.length; i++) {
      if (steps.steps[i].cumulative_cost <= v) s = i; else break;
    }
    return s;
  };

  const chart = buildCurve(curve.points);
  const waffle = buildWaffle(nB);
  const audio = makeAudio();
  const state = { t: 0, budget: 0, step: -1, arrival: baseGrid };
  let maxFresh = 1;

  function render() {
    const { t, arrival } = state;
    let hit = 0, saved = 0;
    for (let i = 0; i < nB; i++) {
      const cb = arrival[cells[i]], bb = baseGrid[cells[i]];
      if (cb <= t) { states[i] = 1; hit++; }                    // burned — stays red
      else if (bb <= H && cb > H) {                             // saved by breaks
        states[i] = 2;
        if (bb <= t) saved++;   // counts up as the baseline front would pass it
      } else states[i] = 0;                                     // standing
    }
    const fresh = fire.draw(arrival, t);
    maxFresh = Math.max(maxFresh, fresh);
    audio.setLevel(fresh / maxFresh);
    houses.redraw();
    waffle.draw(hit, saved);
    $('stat-hit').textContent = hit.toLocaleString();
    $('stat-saved').textContent = saved.toLocaleString();
    $('time-label').textContent = fmtTime(t);
  }

  function updateReadout() {
    const s = state.step, st = s >= 0 ? steps.steps[s] : null;
    const mb = st ? (st.minutes_bought_town == null ? '—' : st.minutes_bought_town) : 0;
    $('budget-readout').textContent =
      `${fmtM2(state.budget)} · ${s + 1} break${s === 0 ? '' : 's'} · ` +
      `${(st ? st.cumulative_saved : 0).toLocaleString()} homes saved · ${mb} min bought`;
  }

  function setBudgetValue(v, force) {
    state.budget = v;
    const s = stepForBudget(v);
    if (s !== state.step || force) {
      state.step = s;
      state.arrival = gridForStep(s);
      rebuildBreakMask(s);
      const st = s >= 0 ? steps.steps[s] : null;
      $('stat-spent').textContent = st ? fmtMoney(st.cumulative_cost) : '$0';
      $('stat-spent').title = st ? `$${st.cumulative_cost.toLocaleString()} spent of ${fmtM2(v)} budget` : '';
      chart.mark(st ? st.cumulative_cost : 0, st ? st.cumulative_saved : 0,
        st ? `${fmtMoney(st.cumulative_cost)} saves ${st.cumulative_saved} homes`
           : '$0 saves 0 homes — move the budget slider');
      render();
    }
    updateReadout();
  }

  // Controls — continuous budget slider (maps to the last step ≤ budget).
  const budgetEl = $('budget'), timeEl = $('time'), playEl = $('play');
  budgetEl.max = String(maxBudget);
  budgetEl.step = '10000';
  $('budget-ticks').innerHTML = [0, ...meta.budgets].map(v =>
    `<span style="left:${(v / maxBudget) * 100}%">${fmtMoney(v)}</span>`).join('');
  let budgetRaf = 0;
  budgetEl.oninput = () => {
    if (!budgetRaf) {
      budgetRaf = requestAnimationFrame(() => {
        budgetRaf = 0;
        setBudgetValue(Number(budgetEl.value));
      });
    }
  };

  timeEl.max = String(H);
  timeEl.oninput = () => { state.t = Number(timeEl.value); render(); };

  const TICK_MS = 20000 / (H / 5);   // full sweep ≈ 20 s
  let timer = null;
  function stopPlay() {
    clearInterval(timer); timer = null;
    playEl.innerHTML = '&#9654;&#xFE0E;'; playEl.setAttribute('aria-label', 'Play');
    audio.setLevel(0);
  }
  function startPlay() {
    if (timer) return;
    if (state.t >= H) { state.t = 0; timeEl.value = '0'; render(); }
    playEl.innerHTML = '&#10074;&#10074;'; playEl.setAttribute('aria-label', 'Pause');
    timer = setInterval(() => {
      state.t = Math.min(state.t + 5, H);
      timeEl.value = String(state.t);
      render();
      if (state.t >= H) stopPlay();
    }, TICK_MS);
  }
  playEl.onclick = () => (timer ? stopPlay() : startPlay());
  document.addEventListener('keydown', e => {
    if (e.code === 'Space' && !/^(BUTTON|INPUT|SELECT|TEXTAREA|SUMMARY)$/.test(e.target.tagName)) {
      e.preventDefault(); playEl.click();
    }
  });

  // Sound toggle — never throws; first click may lazily create the context.
  const soundEl = $('sound');
  const soundLabel = () =>
    { soundEl.textContent = audio.isActive() && !audio.isMuted() ? 'SOUND ON' : 'SOUND OFF'; };
  soundEl.onclick = () => {
    if (!audio.isActive()) audio.start(); else audio.toggleMute();
    soundLabel();
  };

  // Intro: full-bleed title card over the undimmed, still map. Click anywhere skips.
  let startedApp = false;
  function startApp(withAudio) {
    if (startedApp) return;
    startedApp = true;
    if (withAudio) { audio.start(); soundLabel(); }
    document.body.classList.remove('intro');
    const intro = $('intro');
    intro.classList.add('gone');
    setTimeout(() => intro.remove(), 700);
    map.invalidateSize();
    map.fitBounds(bounds, { padding: [10, 10] });
    startPlay();
  }
  $('intro').addEventListener('click', () => startApp(true));

  setBudgetValue(0, true);
  soundLabel();
  if (SKIP_INTRO) startApp(false);
}

main().catch(err => {
  $('story').textContent = `FAILED to load web/${DATA_DIR}/ — ${err.message}`;
  const is = $('intro-story');
  if (is) is.textContent = `FAILED to load web/${DATA_DIR}/ — ${err.message}`;
  console.error(err);
});
