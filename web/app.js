/* Firebreak — renders web/<DATA_DIR>/ (CONTRACT.md) as the one-screen demo.
   No frameworks, no CDN; Leaflet is vendored. DATA_DIR defaults to the real
   pipeline output; towns/index.json (multi-town) or ?data=mock override it.
   URL flags: ?offline=1 forces the offline basemap, ?intro=0 skips the title
   card, ?town=<id> picks a town, ?data=<dir> forces a data dir (testing). */

let DATA_DIR = new URLSearchParams(location.search).get('data') || 'data';
const UNREACHED = 65535;
const TILE_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const PARAMS = new URLSearchParams(location.search);
const FORCE_OFFLINE = PARAMS.has('offline');
const SKIP_INTRO = PARAMS.get('intro') === '0';
const $ = id => document.getElementById(id);

/* Switching region reloads the page against that town's data dir, which replays the
   opening with its own crawl. A reload is the one way to be sure nothing is stale. */
function goToTown(id) {
  const p = new URLSearchParams(location.search);
  p.set('town', id);
  p.delete('data');
  location.search = p.toString();
}

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
    // Edge feather: fire alpha → 0 over the outer 25 cells, hard zero in the
    // outermost 5, so the fire never ends in the grid's straight boundary.
    const ef = this._ef = new Float32Array(this._rows * this._cols);
    for (let r = 0; r < this._rows; r++) {
      for (let c2 = 0; c2 < this._cols; c2++) {
        const dE = Math.min(r, c2, this._rows - 1 - r, this._cols - 1 - c2);
        ef[r * this._cols + c2] = dE < 5 ? 0 : dE >= 25 ? 1 : (dE - 5) / 20;
      }
    }
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
  setBreaks(mask, cellBreak, edgeDir) {
    this._mask = mask; this._cellBreak = cellBreak; this._edir = edgeDir;
  },
  setHover(bi) { this._hover = bi; },
  // Homes live INSIDE this canvas (2×2 blocks at hx/hy in 2× grid px, sub-cell
  // jitter preserved) so they can never move independently of the grid.
  setHomes(hx, hy, states) { this._hx = hx; this._hy = hy; this._hst = states; },
  draw(arrival, t) {
    const frame = this._frame = (this._frame + 1) & 1023;
    const cols = this._cols, W2 = cols * 2, row4 = W2 * 4;
    const d = this._img.data, gd = this._gimg.data;
    const mask = this._mask, cb = this._cellBreak, hover = this._hover, ef = this._ef;
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
        if (mask && mask[i]) {
          // burnt-over cleared ground keeps reading as cleared ground: a pale scar
          R = (R + 217 * 1.2) / 2.2; G = (G + 201 * 1.2) / 2.2; B = (B + 163 * 1.2) / 2.2;
        }
        A *= ef[i];                               // fade out at the grid boundary
        if (age <= GLOW_SPAN) {                   // fresh ignition: amber-white glow
          gd[go] = 255; gd[go + 1] = 226; gd[go + 2] = 150;
          gd[go + 3] = Math.round((220 - (220 * age) / GLOW_SPAN) * ef[i]);
        } else gd[go + 3] = 0;
      } else {
        gd[go + 3] = 0;
        if (mask && mask[i]) {                    // cleared ground (fuel break)
          if (hover >= 0 && cb[i] === hover) { R = 61; G = 220; B = 132; A = 217; }
          else if (this._edir && this._edir[i]) {
            // strip perimeter: 1 px (sub-cell) lighter rim at 0.35 alpha
            const eb = this._edir[i];
            const put = (p, rim) => {
              if (rim) { d[p] = 233; d[p + 1] = 220; d[p + 2] = 188; d[p + 3] = 89; }
              else { d[p] = 217; d[p + 1] = 201; d[p + 2] = 163; d[p + 3] = 217; }
            };
            put(o, (eb & 1) || (eb & 4));
            put(o + 4, (eb & 1) || (eb & 8));
            put(o + row4, (eb & 2) || (eb & 4));
            put(o + row4 + 4, (eb & 2) || (eb & 8));
            continue;
          } else { R = 217; G = 201; B = 163; A = 217; }                   // #d9c9a3 .85
        }
      }
      d[o] = R; d[o + 1] = G; d[o + 2] = B; d[o + 3] = A;
      d[o + 4] = R; d[o + 5] = G; d[o + 6] = B; d[o + 7] = A;
      d[o + row4] = R; d[o + row4 + 1] = G; d[o + row4 + 2] = B; d[o + row4 + 3] = A;
      d[o + row4 + 4] = R; d[o + row4 + 5] = G; d[o + row4 + 6] = B; d[o + row4 + 7] = A;
    }
    if (this._hx) {
      const hx = this._hx, hy = this._hy, hst = this._hst;
      for (let i = 0; i < hx.length; i++) {
        const x = hx[i], y = hy[i], o = (y * W2 + x) * 4;
        // same edge feather as the fire, so the built-up area never ends in a
        // straight line along the grid boundary
        const fade = ef[((y >> 1) * cols) + (x >> 1)];
        if (fade <= 0) continue;
        const burned = hst[i] === 1;
        const R = burned ? 255 : 216, G = burned ? 59 : 216, B = burned ? 59 : 211;
        const A = (burned ? 255 : 204) * fade;
        for (const p of [o, o + 4, o + row4, o + row4 + 4]) {
          d[p] = R; d[p + 1] = G; d[p + 2] = B; d[p + 3] = A;
        }
        if (hst[i] === 2) {          // saved: 1 px green ring around the block
          const ringA = 210 * fade;
          const top = o - row4 - 4, bot = o + 2 * row4 - 4;
          for (let k = 0; k < 4; k++) {
            for (const p of [top + k * 4, bot + k * 4]) {
              d[p] = 61; d[p + 1] = 220; d[p + 2] = 132; d[p + 3] = ringA;
            }
          }
          for (const p of [o - 4, o + 8, o + row4 - 4, o + row4 + 8]) {
            d[p] = 61; d[p + 1] = 220; d[p + 2] = 132; d[p + 3] = ringA;
          }
        }
      }
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

/* Ghost of the baseline burn perimeter (state C): dashed white outline drawn from
   merged horizontal/vertical boundary runs of the baseline burned mask. */
const GhostLayer = L.Layer.extend({
  initialize(bounds, rows, cols, runs, opts) {
    L.setOptions(this, opts);
    this._b = bounds; this._rows = rows; this._cols = cols; this._runs = runs;
  },
  onAdd() {
    const c = this._canvas = L.DomUtil.create('canvas', 'ghost-canvas leaflet-zoom-animated');
    c.width = this._cols * 2; c.height = this._rows * 2;
    const x = c.getContext('2d');
    x.strokeStyle = 'rgba(255,255,255,0.55)';
    x.lineWidth = 1;
    x.setLineDash([5, 4]);
    x.beginPath();
    for (const [x0, y0, x1, y1] of this._runs) {
      x.moveTo(x0 * 2, y0 * 2); x.lineTo(x1 * 2, y1 * 2);
    }
    x.stroke();
    this.getPane().appendChild(c);
    this._reset();
  },
  onRemove() { this._canvas.remove(); },
  getEvents() {
    const ev = { zoom: this._reset, viewreset: this._reset };
    if (this._zoomAnimated) ev.zoomanim = this._animateZoom;
    return ev;
  },
  _reset() {
    const nw = this._map.latLngToLayerPoint(this._b.getNorthWest());
    const se = this._map.latLngToLayerPoint(this._b.getSouthEast());
    L.DomUtil.setPosition(this._canvas, nw);
    this._canvas.style.width = `${se.x - nw.x}px`;
    this._canvas.style.height = `${se.y - nw.y}px`;
  },
  _animateZoom(e) {
    const nb = this._map._latLngBoundsToNewLayerBounds(this._b, e.zoom, e.center);
    L.DomUtil.setTransform(this._canvas, nb.min, this._map.getZoomScale(e.zoom));
  },
});

/* Boundary of {arrival <= H}, as merged straight runs in cell coordinates.
   Edges on the domain border are skipped — where the fire runs off-grid there is
   no real perimeter to draw. */
function boundaryRuns(grid, rows, cols, H) {
  const burned = (r, c) => r >= 0 && r < rows && c >= 0 && c < cols && grid[r * cols + c] <= H;
  const runs = [];
  for (let r = 1; r < rows; r++) {           // horizontal edges at y = r (interior)
    let start = -1;
    for (let c = 0; c <= cols; c++) {
      const edge = c < cols && burned(r, c) !== burned(r - 1, c);
      if (edge && start < 0) start = c;
      if (!edge && start >= 0) { runs.push([start, r, c, r]); start = -1; }
    }
  }
  for (let c = 1; c < cols; c++) {           // vertical edges at x = c (interior)
    let start = -1;
    for (let r = 0; r <= rows; r++) {
      const edge = r < rows && burned(r, c) !== burned(r, c - 1);
      if (edge && start < 0) start = r;
      if (!edge && start >= 0) { runs.push([c, start, c, r]); start = -1; }
    }
  }
  return runs;
}

/* Tween a numeric display over ~150 ms (big numbers). */
const tweens = new Map();
function setNum(el, target, fmt) {
  const prev = tweens.get(el);
  if (prev && prev.target === target) return;
  if (!prev && el.textContent !== '–') {
    // seed from nothing: jump straight there on first write
  }
  const from = prev ? prev.value : target;
  const t0 = performance.now();
  const tw = { target, value: from };
  tweens.set(el, tw);
  const tick = now => {
    if (tweens.get(el) !== tw) return;
    const k = Math.min(1, (now - t0) / 150);
    tw.value = from + (target - from) * k;
    el.textContent = fmt(Math.round(tw.value));
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

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
    return { props: ft.properties, step: ft.properties.step ?? bi + 1, cells };
  });
  return { breaks, cellBreak };
}

/* Fire crackle, synthesized — filtered brown-noise bed + random short bandpassed
   impulses. Level tracks the active front size. Must never throw. */
function makeAudio() {
  let ctx = null, master = null, muted = false, level = 0;
  let windGain = null;            // quiet low bed under the opening crawl
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

      // wind bed: the same noise, far lower and slowly breathing, straight to output
      const wsrc = ctx.createBufferSource(); wsrc.buffer = buf; wsrc.loop = true;
      const wlp = ctx.createBiquadFilter(); wlp.type = 'lowpass'; wlp.frequency.value = 190;
      windGain = ctx.createGain(); windGain.gain.value = 0;
      const lfo = ctx.createOscillator(); lfo.frequency.value = 0.07;
      const lfoGain = ctx.createGain(); lfoGain.gain.value = 0.018;
      lfo.connect(lfoGain); lfoGain.connect(windGain.gain);
      wsrc.connect(wlp); wlp.connect(windGain); windGain.connect(ctx.destination);
      wsrc.start(); lfo.start();
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
  function setWind(on) {
    if (!ctx || !windGain) return;
    try {
      const target = on && !muted ? 0.05 : 0;
      windGain.gain.cancelScheduledValues(ctx.currentTime);
      windGain.gain.setValueAtTime(windGain.gain.value, ctx.currentTime);
      windGain.gain.linearRampToValueAtTime(target, ctx.currentTime + 1.4);
    } catch (e) { /* audio must never break the page */ }
  }
  function toggleMute() {
    muted = !muted;
    if (!ctx) return muted;
    try {
      master.gain.value = muted ? 0 : Math.min(0.5, level * 0.5);
      if (windGain) windGain.gain.value = muted ? 0 : windGain.gain.value;
    } catch (e) { /* ignore */ }
    return muted;
  }
  return { start, setLevel, setWind, toggleMute, isMuted: () => muted, isActive: () => !!ctx };
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
  const cv = $('waffle'), COLS = 20, CELL = 12, SQ = 10;
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

/* The dense per-step model (steps.json array + breaks.geojson). CONTRACT.md: when
   those files are absent, degrade to the five snap budgets in solutions.json. */
async function loadModel() {
  try {
    const [arr, breaksFC] = await Promise.all([
      loadJSON('steps.json'), loadJSON('breaks.geojson'),
    ]);
    let cum = 0;
    const steps = arr.map(s => {
      cum += (s.break_ids || []).length;
      return {
        cost: s.cumulative_cost, saved: s.cumulative_saved,
        minutesBought: s.minutes_bought == null ? null : s.minutes_bought,
        b64u8: s.arrival_b64, breakCount: cum,
      };
    });
    return { steps, breaksFC, dense: true, solver: true };
  } catch (err) {
    console.warn(`steps.json or breaks.geojson unavailable (${err.message}), trying snap budgets`);
    let solutions;
    try {
      solutions = await loadJSON('solutions.json');
    } catch (err2) {
      // a free-play region (story:false): no solver output at all, and that is fine
      return {
        steps: [{ cost: 0, saved: 0, minutesBought: 0, breakCount: 0 }],
        breaksFC: { type: 'FeatureCollection', features: [] }, dense: false, solver: false,
      };
    }
    const feats = [], seen = new Set();
    const steps = [{ cost: 0, saved: 0, minutesBought: 0, breakCount: 0 }];
    solutions.forEach((sol, i) => {
      for (const f of sol.breaks.features) {
        if (!seen.has(f.properties.id)) {
          seen.add(f.properties.id);
          feats.push({ type: 'Feature', geometry: f.geometry,
            properties: Object.assign({}, f.properties, { step: i + 1 }) });
        }
      }
      steps.push({
        cost: sol.cost, saved: sol.stats.houses_saved,
        minutesBought: sol.stats.minutes_bought_town == null ? null : sol.stats.minutes_bought_town,
        b64u16: sol.arrival_min_b64, breakCount: seen.size,
      });
    });
    return { steps, breaksFC: { type: 'FeatureCollection', features: feats }, dense: false, solver: true };
  }
}

async function main() {
  // Town selector (CONTRACT.md web/towns/index.json): >1 entries → dropdown;
  // missing file or a single entry → none. Selecting reloads with ?town=<id>.
  let towns = [], entry = null;
  try {
    const tr = await fetch('towns/index.json');
    if (tr.ok) {
      const j = await tr.json();
      if (Array.isArray(j)) towns = j;
    }
  } catch (e) { /* no manifest, fall back to the default data dir */ }
  if (towns.length) {
    const want = PARAMS.get('town');
    entry = towns.find(t => t.id === want) || towns[0];
    if (!PARAMS.get('data')) DATA_DIR = entry.data_dir.replace(/\/+$/, '');
    if (towns.length > 1) {
      const sel = $('town-select');
      sel.hidden = false;
      document.body.classList.add('has-towns');
      sel.innerHTML = towns.map(t =>
        `<option value="${t.id}"${t.id === entry.id ? ' selected' : ''}>${t.name} · ${t.event}</option>`).join('');
      sel.onchange = () => goToTown(sel.value);
    }
  }

  const meta = await loadJSON('meta.json');
  const optional = (name, fallback) => loadJSON(name).catch(() => fallback);
  const [baseline, curve, buildingsFC, legend, model] = await Promise.all([
    loadJSON('baseline.json'), optional('curve.json', { points: [] }),
    loadJSON('buildings.geojson'), optional('fuel_legend.json', []), loadModel(),
  ]);
  const { rows, cols } = meta.grid, b = meta.bounds;
  const bounds = L.latLngBounds([b.south, b.west], [b.north, b.east]);
  const H = meta.horizon_min;
  const maxBudget = meta.budgets[meta.budgets.length - 1];

  $('story').textContent = meta.story;
  $('legend').innerHTML = legend.map(g =>
    `<span class="chip"><i style="background:${g.color}"></i>${g.group}</span>`).join('');
  $('simp-list').innerHTML = meta.simplifications.map(s => `<li>${s}</li>`).join('');

  const map = L.map('map', {
    zoomSnap: 0.25, minZoom: 4, maxZoom: 17,
    zoomControl: false,                 // rebuilt top right, clear of the brand
    wheelPxPerZoomLevel: 45, wheelDebounceTime: 12,   // fast, smooth wheel zoom
  });
  L.control.zoom({ position: 'topright' }).addTo(map);
  map.fitBounds(bounds, { padding: [10, 10] });
  map.createPane('fire').style.zIndex = 405;
  window._fb = { map, bounds: b, rows, cols };   // debug/test handle

  /* "US" flies out to the continental view, where every region shows as a marker. */
  const US_BOUNDS = L.latLngBounds([24.5, -125.0], [49.4, -66.9]);
  const UsControl = L.Control.extend({
    options: { position: 'topright' },
    onAdd() {
      const el = L.DomUtil.create('button', 'us-btn mono');
      el.type = 'button';
      el.textContent = 'US';
      el.title = 'Zoom out to the United States';
      L.DomEvent.disableClickPropagation(el);
      L.DomEvent.on(el, 'click', () => map.flyToBounds(US_BOUNDS, { duration: 1.6 }));
      return el;
    },
  });
  map.addControl(new UsControl());

  /* Below zoom 9 the region markers replace the fire: one per town in the index.
     Centres come from each town's own meta.bounds, fetched once, in the background. */
  const regionLayer = L.layerGroup();
  (async () => {
    for (const t of towns) {
      let ll = null;
      if (entry && t.id === entry.id) ll = bounds.getCenter();
      else {
        try {
          const r = await fetch(`${t.data_dir.replace(/\/+$/, '')}/meta.json`);
          if (!r.ok) continue;
          const m = await r.json();
          ll = L.latLng((m.bounds.north + m.bounds.south) / 2, (m.bounds.east + m.bounds.west) / 2);
        } catch (e) { continue; }
      }
      L.marker(ll, {
        icon: L.divIcon({
          className: 'region-pin', iconSize: [0, 0],
          html: `<span class="region-dot"></span><span class="region-label">` +
                `<b>${t.name}</b>${t.event ? `<i>${t.event}</i>` : ''}</span>`,
        }),
      }).on('click', () => goToTown(t.id)).addTo(regionLayer);
    }
    syncRegionPins();
  })();
  function syncRegionPins() {
    const show = map.getZoom() < 9 && regionLayer.getLayers().length > 0;
    if (show && !map.hasLayer(regionLayer)) regionLayer.addTo(map);
    if (!show && map.hasLayer(regionLayer)) map.removeLayer(regionLayer);
    document.body.classList.toggle('wide-view', map.getZoom() < 9);
  }
  map.on('zoomend', syncRegionPins);

  const windDir = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(meta.wind.from_deg / 45) % 8];
  const statusBits = mode => [
    mode, `${Math.round(meta.grid.cell_m)} M CELLS`,
    `WIND ${windDir} ${meta.wind.speed_mph} MPH`,
  ].join(' · ');
  setupBasemap(map, bounds, mode => {
    $('basemap-status').textContent = statusBits(mode);
    $('legend').hidden = !mode.startsWith('OFFLINE');
  });

  // Buildings → flat arrays; homes render inside the fire canvas (2× grid px,
  // mercator-correct sub-cell position, clamped so the saved-ring fits).
  const feats = buildingsFC.features, nB = feats.length;
  const cells = new Uint32Array(nB), states = new Uint8Array(nB);
  const hx = new Int32Array(nB), hy = new Int32Array(nB);
  const yN = merc(b.north), yS = merc(b.south);
  const W2 = cols * 2, H2 = rows * 2;
  feats.forEach((f, i) => {
    cells[i] = f.properties.row * cols + f.properties.col;
    const [lon, lat] = f.geometry.coordinates;
    const gx = ((lon - b.west) / (b.east - b.west)) * W2;
    const gy = ((yN - merc(lat)) / (yN - yS)) * H2;
    hx[i] = Math.min(W2 - 3, Math.max(1, Math.round(gx) - 1));
    hy[i] = Math.min(H2 - 3, Math.max(1, Math.round(gy) - 1));
  });
  $('homes-label').textContent = `02 HOMES SAVED / ${nB.toLocaleString()}`;

  const fire = new FireLayer(bounds, rows, cols, { pane: 'fire' }).addTo(map);
  fire.setHomes(hx, hy, states);
  // 6:30 AM is the Camp Fire's ignition time — Paradise-only until meta grows a field.
  const ignTime = meta.town.startsWith('Paradise') ? ' · 6:30 AM' : '';
  const ignMarker = L.marker([meta.ignition.lat, meta.ignition.lon], {
    interactive: false, keyboard: false,
    icon: L.divIcon({
      className: 'ign', iconSize: [0, 0],
      html: `<span class="ign-dot"></span><span class="ign-label">${meta.ignition.label.split(' (')[0]}${ignTime}</span>`,
    }),
  }).addTo(map);

  // Breaks → cells; mask marks active break cells, edge bits their outward rim.
  const braster = rasterizeBreaks(model.breaksFC, meta);
  const breakMask = new Uint8Array(rows * cols);
  const breakEdge = new Uint8Array(rows * cols);   // bits: 1 N, 2 S, 4 W, 8 E
  fire.setBreaks(breakMask, braster.cellBreak, breakEdge);
  /* Edge bits mark which sides of a cleared cell face open ground, so the strip
     gets a lighter rim. Shared by the solver's breaks and hand-drawn ones. */
  function computeBreakEdges(cellList) {
    for (const c of cellList) {
      const r = (c / cols) | 0, cc = c % cols;
      let e = 0;
      if (r === 0 || !breakMask[c - cols]) e |= 1;
      if (r === rows - 1 || !breakMask[c + cols]) e |= 2;
      if (cc === 0 || !breakMask[c - 1]) e |= 4;
      if (cc === cols - 1 || !breakMask[c + 1]) e |= 8;
      breakEdge[c] = e;
    }
  }
  function rebuildBreakMask(stepIdx) {
    breakMask.fill(0); breakEdge.fill(0);
    const active = [];
    for (const bk of braster.breaks) {
      if (bk.step <= stepIdx) for (const c of bk.cells) { breakMask[c] = 1; active.push(c); }
    }
    computeBreakEdges(active);
  }
  function setMaskFromCells(cellList) {
    breakMask.fill(0); breakEdge.fill(0);
    for (const c of cellList) breakMask[c] = 1;
    computeBreakEdges(cellList);
  }

  // Grids: step 0 = baseline (uint16 from baseline.json); later steps decode their
  // uint8 bucket grids — or uint16 grids in the snap-budget fallback — on demand.
  const baseGrid = decodeGrid(baseline.arrival_min_b64, rows, cols);
  const stepGrids = [];
  const gridForStep = s => {
    if (s === 0) return baseGrid;
    if (stepGrids[s]) return stepGrids[s];
    const st = model.steps[s];
    return (stepGrids[s] = st.b64u8
      ? decodeStepGrid(st.b64u8, rows, cols, 5, 255)
      : decodeGrid(st.b64u16, rows, cols));
  };
  const stepForBudget = v => {
    let s = 0;
    for (let i = 0; i < model.steps.length; i++) {
      if (model.steps[i].cost <= v) s = i; else break;
    }
    return s;
  };

  if (!model.solver) {          // free-play region: no budget, no curve to show
    $('budget-block').hidden = true;
    $('info-card').querySelector('h2').hidden = true;
    $('curve').hidden = true;
    $('curve-sentence').hidden = true;
  }
  const chart = buildCurve(curve.points);
  const waffle = buildWaffle(nB);
  const audio = makeAudio();
  const state = { t: 0, budget: 0, step: 0, arrival: baseGrid };
  let fpBaseGrid = null;      // free play's own no-breaks run, for "homes saved"
  let maxFresh = 1;
  let flow = 'crawl';   // walkthrough state (see setWalk)
  const skipEl = $('skip');
  const STATE_ZOOM = 6;                       // state scale, where the opening starts
  const townCenter = bounds.getCenter();      // the whole burn area, town included
  const targetZoom = map.getBoundsZoom(bounds, false, L.point(24, 24));
  let lastCounts = { hit: 0, saved: 0 };

  const fmtInt = n => n.toLocaleString();
  function updateStats() {
    setNum($('stat-saved'), lastCounts.saved, fmtInt);
    if (flow === 'free') {
      // free play has its own spend and its own delay, not the solver's
      $('stat-line').textContent =
        `SPENT ${fmtMoney(fp.cost || 0)} · +${Math.round(fp.minutesBought || 0)} MIN TO FIRST HOME`;
      return;
    }
    const st = model.steps[state.step];
    const mb = st.minutesBought == null ? '0' : `+${Math.round(st.minutesBought)}`;
    $('stat-line').textContent = `SPENT ${fmtMoney(st.cost)} · ${mb} MIN EVACUATION`;
  }

  function render() {
    const { t, arrival } = state;
    const ref = fpBaseGrid || baseGrid;    // free play compares against its own baseline
    let hit = 0, saved = 0;
    for (let i = 0; i < nB; i++) {
      const cb = arrival[cells[i]], bb = ref[cells[i]];
      if (cb <= t) { states[i] = 1; hit++; }                    // burned — stays red
      else if (bb <= H && cb > H) {                             // saved by breaks
        states[i] = 2;
        if (bb <= t) saved++;   // counts up as the baseline front would pass it
      } else states[i] = 0;                                     // standing
    }
    lastCounts = { hit, saved };
    const fresh = fire.draw(arrival, t);
    maxFresh = Math.max(maxFresh, fresh);
    audio.setLevel(fresh / maxFresh);
    waffle.draw(hit, saved);
    updateStats();
    $('time-label').textContent = fmtTime(t);
  }

  function updateReadout() {
    const st = model.steps[state.step];
    const mb = st.minutesBought == null ? '0' : Math.round(st.minutesBought);
    $('budget-readout').textContent =
      `${fmtM2(state.budget)} · ${st.breakCount} break${st.breakCount === 1 ? '' : 's'} · ` +
      `${st.saved.toLocaleString()} homes saved · ${mb} min bought`;
  }

  function setBudgetValue(v, force) {
    state.budget = v;
    const s = stepForBudget(v);
    if (s !== state.step || force) {
      state.step = s;
      state.arrival = gridForStep(s);
      rebuildBreakMask(s);
      const st = model.steps[s];
      chart.mark(st.cost, st.saved,
        s > 0 ? `${fmtMoney(st.cost)} saves ${st.saved} homes`
              : 'Move the budget slider.');
      render();
    }
    updateReadout();
    // first drag in the pick state reveals the replay button
    if (flow === 'pick' && state.budget > 0 && capBtn.hidden) {
      capBtn.hidden = false;
      capBtn.textContent = 'Run it again';
    }
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
  timeEl.oninput = () => {
    state.t = Number(timeEl.value);
    render();
    if (state.t >= H) { if (timer) stopPlay(); onRunEnd(); }
  };

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
      if (state.t >= H) { stopPlay(); onRunEnd(); }
    }, TICK_MS);
  }
  playEl.onclick = () => (timer ? stopPlay() : startPlay());
  document.addEventListener('keydown', e => {
    if (e.code === 'Space' && !/^(BUTTON|INPUT|SELECT|TEXTAREA|SUMMARY)$/.test(e.target.tagName)) {
      e.preventDefault(); playEl.click();
    }
  });

  /* ---------------- free play: your own fire, wind and breaks ----------------
     The model runs in a Web Worker (sim.js), proven cell-for-cell against
     parity.json. Every change re-runs two sims: one without the drawn breaks to
     get a fair baseline, one with them. */
  const fp = {
    on: false, ready: false, drawing: false, busy: false,
    ignition: null, wind: null, breaks: [], cells: new Set(), fuel: null, queued: false,
  };
  let worker = null, runSeq = 0;

  function fpSetHint(text) { $('fp-hint').textContent = text; }
  function fpSetResult(text) { $('fp-result').textContent = text; }

  function workerRun(breaksArr, wind, ignition) {
    return new Promise((resolve, reject) => {
      const id = ++runSeq;
      const onMsg = ev => {
        const m = ev.data;
        if (m.type === 'result' && m.id === id) {
          worker.removeEventListener('message', onMsg);
          resolve(m);
        } else if (m.type === 'error') {
          worker.removeEventListener('message', onMsg);
          reject(new Error(m.message));
        }
      };
      worker.addEventListener('message', onMsg);
      worker.postMessage({ type: 'run', id, ignition_rc: ignition, wind, breaks: breaksArr });
    });
  }

  function bucketsToMinutes(buckets, bucketMin) {
    const out = new Uint16Array(buckets.length);
    for (let i = 0; i < buckets.length; i++) {
      out[i] = buckets[i] === 255 ? UNREACHED : buckets[i] * bucketMin;
    }
    return out;
  }

  const COST_KEY = [null, 'grass', 'shrub', 'shrub', 'timber', 'timber', 'timber', null];
  function breakCost(cellList) {
    if (!fp.fuel || !fp.costPerAcre) return 0;
    let acres = 0, total = 0;
    for (const c of cellList) {
      const key = COST_KEY[fp.fuel[c]];
      if (!key) continue;
      acres += fp.cellAcres;
      total += fp.cellAcres * (fp.costPerAcre[key] || 0);
    }
    return Math.round(total);
  }

  async function fpRun() {
    if (!fp.ready || fp.busy) { fp.queued = true; return; }
    fp.busy = true;
    try {
      const all = [...fp.cells];
      $('fp-sim').textContent = 'running';
      const [bare, withBreaks] = await Promise.all([
        workerRun([], fp.wind, fp.ignition),
        all.length ? workerRun(all, fp.wind, fp.ignition) : null,
      ].filter(Boolean));
      const use = withBreaks || bare;
      state.arrival = bucketsToMinutes(use.buckets, fp.bucketMin);
      fpBaseGrid = bucketsToMinutes(bare.buckets, fp.bucketMin);
      setMaskFromCells(all);
      const lost = use.stats.homes_hit;
      const saved = Math.max(0, bare.stats.homes_hit - lost);
      const cost = breakCost(all);
      fp.cost = cost;
      fp.minutesBought = Math.max(0,
        (use.stats.minutes_to_first_home || 0) - (bare.stats.minutes_to_first_home || 0));
      state.t = H;
      timeEl.value = String(H);
      render();                       // after the numbers, so the panel shows them
      $('fp-sim').textContent = `${Math.round(use.stats.sim_seconds * 1000)} ms`;
      fpSetResult(all.length
        ? `${saved.toLocaleString()} homes saved · ${fmtMoney(cost)} · ${lost.toLocaleString()} lost`
        : `${lost.toLocaleString()} of ${nB.toLocaleString()} homes lost`);
    } catch (err) {
      $('fp-sim').textContent = 'failed';
      fpSetResult(`Simulation failed. ${err.message}`);
      console.error(err);
    } finally {
      fp.busy = false;
      if (fp.queued) { fp.queued = false; fpRun(); }
    }
  }

  function setWindDial(deg) {
    fp.wind.from_deg = ((deg % 360) + 360) % 360;
    $('wd-arrow').setAttribute('transform', `rotate(${fp.wind.from_deg} 32 32)`);
    const dir = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(fp.wind.from_deg / 45) % 8];
    $('wind-read').textContent = `WIND ${dir} ${fp.wind.speed_mph} MPH`;
  }

  function latLngToCell(ll) {
    const fyF = (yN - merc(ll.lat)) / (yN - yS);
    const fxF = (ll.lng - b.west) / (b.east - b.west);
    if (fxF < 0 || fxF >= 1 || fyF < 0 || fyF >= 1) return null;
    return [Math.floor(fyF * rows), Math.floor(fxF * cols)];
  }

  /* A drawn line clears every cell whose centre is within one cell of it, which
     is the two-cell width the solver uses. */
  function addBreakLine(path) {
    const added = [];
    for (let s = 0; s < path.length - 1; s++) {
      const a = path[s], c2 = path[s + 1];
      const steps = Math.max(1, Math.ceil(Math.hypot(c2[0] - a[0], c2[1] - a[1]) * 2));
      for (let k = 0; k <= steps; k++) {
        const r = a[0] + (c2[0] - a[0]) * k / steps;
        const c = a[1] + (c2[1] - a[1]) * k / steps;
        for (let dr = -1; dr <= 1; dr++) {
          for (let dc = -1; dc <= 1; dc++) {
            const rr = Math.round(r) + dr, cc = Math.round(c) + dc;
            if (rr < 0 || rr >= rows || cc < 0 || cc >= cols) continue;
            if (Math.hypot(rr - r, cc - c) > 1.0) continue;
            const idx = rr * cols + cc;
            if (!fp.cells.has(idx)) { fp.cells.add(idx); added.push(idx); }
          }
        }
      }
    }
    if (added.length) fp.breaks.push(added);
    return added.length;
  }

  function freePlayReady() {
    if (fp.on) return;
    fp.on = true;
    $('freeplay').hidden = false;
    if (!worker) startWorker();
  }

  function startWorker() {
    try {
      worker = new Worker('sim.js');
    } catch (err) {
      fpSetHint('Free play needs a local server.');
      return;
    }
    worker.addEventListener('message', ev => {
      const m = ev.data;
      if (m.type === 'ready') {
        fp.ready = true;
        fp.bucketMin = m.bucket_min || 5;
        fp.fuel = m.fuel;
        fp.costPerAcre = m.cost_per_acre;
        fp.cellAcres = m.cell_acres;
        fp.ignition = m.ignition_rc.slice();
        fp.wind = Object.assign({}, m.wind);
        $('wind-speed').value = String(fp.wind.speed_mph);
        setWindDial(fp.wind.from_deg);
        fpRun();
      } else if (m.type === 'error') {
        $('fp-sim').textContent = 'failed';
        fpSetResult(`Simulation failed. ${m.message}`);
      }
    });
    worker.postMessage({ type: 'load', dataDir: DATA_DIR });
  }

  // map interaction: click sets the ignition, drag draws a break
  let drawPath = null;
  map.on('click', e => {
    if (!fp.on || fp.drawing) return;
    const rc = latLngToCell(e.latlng);
    if (!rc) return;
    fp.ignition = rc;
    ignMarker.setLatLng(e.latlng);
    fpSetHint('Draw a break, or click again to move the fire.');
    fpRun();
  });
  map.on('mousedown', e => {
    if (!fp.on || !fp.drawing) return;
    const rc = latLngToCell(e.latlng);
    if (!rc) return;
    drawPath = [rc];
    map.dragging.disable();
  });
  map.on('mousemove', e => {
    if (!drawPath) return;
    const rc = latLngToCell(e.latlng);
    if (!rc) return;
    const last = drawPath[drawPath.length - 1];
    if (rc[0] !== last[0] || rc[1] !== last[1]) drawPath.push(rc);
  });
  map.on('mouseup', () => {
    if (!drawPath) return;
    const path = drawPath;
    drawPath = null;
    map.dragging.enable();
    if (path.length < 2) return;
    if (addBreakLine(path)) fpRun();
  });

  $('fp-draw').onclick = () => {
    fp.drawing = !fp.drawing;
    $('fp-draw').classList.toggle('on', fp.drawing);
    document.body.classList.toggle('drawing', fp.drawing);
    fpSetHint(fp.drawing ? 'Drag across the map to cut a break.' : 'Click the map to move the fire.');
  };
  $('fp-undo').onclick = () => {
    const last = fp.breaks.pop();
    if (!last) return;
    last.forEach(c => fp.cells.delete(c));
    fpRun();
  };
  $('fp-clear').onclick = () => {
    fp.breaks = [];
    fp.cells.clear();
    fpRun();
  };
  $('wind-speed').oninput = () => {
    if (!fp.wind) return;
    fp.wind.speed_mph = Number($('wind-speed').value);
    setWindDial(fp.wind.from_deg);
    fpRun();
  };
  (() => {                       // drag the dial to set wind direction
    const dial = $('wind-dial');
    let dragging = false;
    const angleFrom = ev => {
      const r = dial.getBoundingClientRect();
      const dx = ev.clientX - (r.left + r.width / 2);
      const dy = ev.clientY - (r.top + r.height / 2);
      return Math.round((Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360 / 5) * 5;
    };
    dial.addEventListener('mousedown', ev => { dragging = true; setWindDial(angleFrom(ev)); ev.preventDefault(); });
    window.addEventListener('mousemove', ev => { if (dragging) setWindDial(angleFrom(ev)); });
    window.addEventListener('mouseup', () => { if (dragging) { dragging = false; fpRun(); } });
  })();

  /* Region search. Needs serve.py: /api/health decides whether the box exists at
     all, so a plain static server simply shows nothing rather than something broken.
     Geocoding is Nominatim, which needs no key. */
  const fpStatus = $('fp-status');
  async function apiHealthy() {
    try {
      const r = await fetch('api/health', { signal: AbortSignal.timeout(4000) });
      if (!r.ok) return false;
      const j = await r.json();
      return !!j.ok;
    } catch (e) { return false; }
  }
  async function geocode(q) {
    const url = 'https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=us&q='
      + encodeURIComponent(q);
    const r = await fetch(url, { headers: { Accept: 'application/json' } });
    if (!r.ok) throw new Error(`Search failed (${r.status}).`);
    const j = await r.json();
    if (!j.length) throw new Error('No place found by that name.');
    return { lat: +j[0].lat, lon: +j[0].lon, name: j[0].display_name.split(',').slice(0, 2).join(',') };
  }
  async function buildRegion(q) {
    fpStatus.textContent = 'Looking up the place.';
    const place = await geocode(q);
    fpStatus.textContent = `Requesting ${place.name}.`;
    const r = await fetch('api/region', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat: place.lat, lon: place.lon, name: place.name }),
    });
    if (!r.ok) throw new Error(`Region build failed (${r.status}).`);
    const job = await r.json();
    if (job.status === 'ready') return job;
    for (let i = 0; i < 300; i++) {                  // poll about once a second
      await new Promise(res => setTimeout(res, 1000));
      const s = await fetch(`api/region/${job.id}/status`);
      if (!s.ok) throw new Error(`Status failed (${s.status}).`);
      const st = await s.json();
      if (st.status === 'ready') return st;
      if (st.status === 'error') throw new Error(st.error || 'Region build failed.');
      fpStatus.textContent = `${st.stage || 'Building'} ${st.pct != null ? st.pct + '%' : ''}`.trim();
    }
    throw new Error('Region build timed out.');
  }
  apiHealthy().then(ok => {
    if (!ok) return;                                  // static server: no search box
    $('fp-search').hidden = false;
    const input = $('fp-place');
    input.onkeydown = async ev => {
      if (ev.key !== 'Enter' || !input.value.trim()) return;
      const q = input.value.trim();
      input.disabled = true;
      try {
        const done = await buildRegion(q);
        fpStatus.textContent = 'Loading the region.';
        goToTown(done.id);
      } catch (err) {
        fpStatus.textContent = err.message;
        input.disabled = false;
      }
    };
  });

  // The one "i": everything that is not budget or homes saved lives behind it.
  const infoModal = $('info-modal');
  const openInfo = () => { infoModal.hidden = false; };
  const closeInfo = () => { infoModal.hidden = true; };
  $('info-open').onclick = openInfo;
  $('info-close').onclick = closeInfo;
  infoModal.onclick = e => { if (e.target === infoModal) closeInfo(); };
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeInfo(); });

  // Sound toggle — never throws; first click may lazily create the context.
  const soundEl = $('sound');
  const soundLabel = () =>
    { soundEl.textContent = audio.isActive() && !audio.isMuted() ? 'SOUND ON' : 'SOUND OFF'; };
  soundEl.onclick = () => {
    if (!audio.isActive()) audio.start(); else audio.toggleMute();
    soundLabel();
  };

  // --- walkthrough: armed → burn0 → pick → burn1 → done, plus free play.
  // Every state has a caption saying what is happening and what to do next. ---
  let ghost = null;
  function showGhost(on) {
    if (on && !ghost) {
      ghost = new GhostLayer(bounds, rows, cols, boundaryRuns(baseGrid, rows, cols, H), { pane: 'fire' });
    }
    if (on && !map.hasLayer(ghost)) ghost.addTo(map);
    if (!on && ghost && map.hasLayer(ghost)) map.removeLayer(ghost);
  }
  const capEl = $('caption'), capText = $('caption-text'), capBtn = $('caption-btn');
  const arrowEl = $('point-arrow');
  const townName = meta.town.split(',')[0];
  function setCaption(text, btn) {
    capEl.hidden = !text;
    capText.textContent = text || '';
    capBtn.hidden = !btn;
    if (btn) capBtn.textContent = btn;
  }
  function pointAtBudget(on) {
    arrowEl.hidden = !on;
    if (on) {
      const r = $('budget-block').getBoundingClientRect();
      arrowEl.style.top = `${r.top + 26}px`;
    }
  }
  function enterFreePlay() {
    setCaption('Now start your own fire.', null);
    if (typeof freePlayReady === 'function') freePlayReady();
  }
  function setWalk(f) {
    flow = f;
    document.body.dataset.flow = f;
    document.body.classList.toggle('crawl', f === 'crawl');
    showGhost(f === 'burn1' || f === 'done');
    pointAtBudget(f === 'pick');
    skipEl.hidden = f === 'free';
    skipEl.textContent = f === 'crawl' ? 'Skip intro' : 'Free play';
    $('timeline').hidden = f === 'crawl';
    audio.setWind(f === 'crawl');      // low wind bed under the opening only
    if (f === 'burn0') {
      setCaption('This is what happened.', null);
      crawlTimers.push(setTimeout(() => {
        if (flow === 'burn0') {
          setCaption('Twelve hours of fire in twenty seconds. Each red square is a home.', null);
        }
      }, 2600));
    }
    if (f === 'pick') setCaption(
      `${lastCounts.hit.toLocaleString()} of ${nB.toLocaleString()} homes gone. ` +
      `What would a budget have bought?`, null);
    if (f === 'burn1') setCaption('Same fire. The pale strips are cleared ground.', null);
    if (f === 'done') {
      const st = model.steps[state.step];
      const mb = st.minutesBought == null ? 0 : Math.round(st.minutesBought);
      setCaption(`${fmtMoney(st.cost)} · ${lastCounts.saved.toLocaleString()} homes saved · ` +
        `${mb} min evacuation time.`, 'Try another budget');
    }
    if (f === 'free') { setCaption(null, null); enterFreePlay(); }
    updateStats();
  }
  function onRunEnd() {
    if (flow === 'burn0') setWalk('pick');
    else if (flow === 'burn1') setWalk('done');
  }
  capBtn.onclick = e => {
    e.stopPropagation();
    if (flow === 'pick') { setWalk('burn1'); state.t = 0; timeEl.value = '0'; render(); startPlay(); }
    else if (flow === 'done') {
      setWalk('pick');
      setCaption('Pick another budget, then run it again.', 'Run it again');
    }
  };

  /* The opening: black, the map fades in at state scale and flies to the town over
     10 s while meta.crawl plays one line at a time, then the fire starts. */
  const crawlEl = $('crawl'), crawlLine = $('crawl-line');
  const crawlLines = (Array.isArray(meta.crawl) && meta.crawl.length)
    ? meta.crawl.slice(0, 6)
    : meta.story.split(/(?<=[.?])\s+/).filter(Boolean).slice(0, 4);
  let crawlTimers = [];
  function clearCrawl() {
    crawlTimers.forEach(clearTimeout);
    crawlTimers = [];
    crawlEl.hidden = true;
    crawlLine.classList.remove('on');
  }
  function startBurn() {
    clearCrawl();
    setWalk('burn0');            // drops the crawl class, so the panel takes its space
    map.invalidateSize();        // then reframe for the narrower map
    map.setView(townCenter, map.getBoundsZoom(bounds, false, L.point(24, 24)), { animate: false });
    state.t = 0; timeEl.value = '0';
    render();
    startPlay();
  }
  function runOpening() {
    setWalk('crawl');
    crawlEl.hidden = false;
    crawlEl.classList.remove('clear');
    map.invalidateSize();        // crawl runs full bleed
    map.setView(townCenter, STATE_ZOOM, { animate: false });
    const at = (ms, fn) => crawlTimers.push(setTimeout(fn, ms));
    at(350, () => {
      crawlEl.classList.add('clear');                       // map reveals behind
      map.flyTo(townCenter, targetZoom, { duration: 10, easeLinearity: 0.25 });
    });
    const HOLD = 2500, FADE = 700;
    crawlLines.forEach((line, i) => {
      at(600 + i * HOLD, () => {
        crawlLine.textContent = line;
        crawlLine.classList.add('on');
      });
      at(600 + i * HOLD + (HOLD - FADE), () => crawlLine.classList.remove('on'));
    });
    at(600 + crawlLines.length * HOLD, startBurn);
  }
  skipEl.onclick = e => {
    e.stopPropagation();
    if (flow === 'crawl') startBurn();
    else { clearCrawl(); setWalk('free'); }
  };

  // --- break hover: cell → active break → green highlight + mono tooltip ---
  const tip = $('break-tip');
  const savedByBreak = new Map(curve.points.map((p, i) =>
    [p.break_id, p.cumulative_saved - (i > 0 ? curve.points[i - 1].cumulative_saved : 0)]));
  let hoverBi = -1;
  map.on('mousemove', e => {
    const fyF = (yN - merc(e.latlng.lat)) / (yN - yS);
    const fxF = (e.latlng.lng - b.west) / (b.east - b.west);
    let bi = -1;
    if (fxF >= 0 && fxF < 1 && fyF >= 0 && fyF < 1) {
      const cell = Math.floor(fyF * rows) * cols + Math.floor(fxF * cols);
      const cand = braster.cellBreak[cell];
      if (cand >= 0 && braster.breaks[cand].step <= state.step) bi = cand;
    }
    if (bi !== hoverBi) {
      hoverBi = bi;
      fire.setHover(bi);
      fire.draw(state.arrival, state.t);
      tip.hidden = bi < 0;
    }
    if (bi >= 0) {
      const bk = braster.breaks[bi];
      tip.textContent =
        `BREAK ${bk.props.id} · STEP ${bk.step} · ${fmtMoney(bk.props.cost)} · ` +
        `${savedByBreak.get(bk.props.id) ?? 0} HOMES PROTECTED`;
      tip.style.left = `${e.containerPoint.x + 14}px`;
      tip.style.top = `${e.containerPoint.y + 14}px`;
    }
  });
  map.on('mouseout', () => {
    if (hoverBi >= 0) { hoverBi = -1; fire.setHover(-1); fire.draw(state.arrival, state.t); }
    tip.hidden = true;
  });

  // Title card. Clicking anywhere on it enters; that click is the audio gesture.
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
    // story:false regions have no solver run and no crawl, so they open in free play
    if (entry && entry.story === false) { setWalk('free'); state.t = H; render(); }
    else runOpening();
  }
  $('intro').addEventListener('click', () => startApp(true));

  /* Test hook: where the grid canvas puts a home versus where Leaflet puts it.
     Uses an interior home (edge homes are clamped so their ring stays on canvas).
     Residual is bounded by half a grid pixel, which is what drawing homes as grid
     pixels costs; it is fixed per home and does not grow or slide with zoom. */
  const probeIdx = (() => {
    let best = 0, bestD = Infinity;
    for (let i = 0; i < nB; i++) {
      const r = (cells[i] / cols) | 0, c = cells[i] % cols;
      const d = Math.abs(r - rows / 2) + Math.abs(c - cols / 2);
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  })();
  window._fb.breakCells = () => [...fp.cells];
  window._fb.homeProbe = () => {
    const cv = document.querySelector('.fire-canvas');
    if (!cv || !nB) return null;
    const r = cv.getBoundingClientRect();
    const mr = $('map').getBoundingClientRect();
    const [lon, lat] = feats[probeIdx].geometry.coordinates;
    const cx = r.left + ((hx[probeIdx] + 1) / (cols * 2)) * r.width;
    const cy = r.top + ((hy[probeIdx] + 1) / (rows * 2)) * r.height;
    const pt = map.latLngToContainerPoint([lat, lon]);
    const gridPx = r.height / (rows * 2);
    return {
      dx: +(cx - (mr.left + pt.x)).toFixed(2), dy: +(cy - (mr.top + pt.y)).toFixed(2),
      gridPx: +gridPx.toFixed(2),
    };
  };

  setBudgetValue(0, true);
  soundLabel();
  if (SKIP_INTRO) { document.body.classList.remove('intro'); $('intro').remove(); startBurn(); }
}

/* Title-card heat haze: drift the turbulence seed and baseFrequency so the red
   copy behind the white word waves. Slow, no flicker, stops once the card is gone. */
function animateHeat() {
  const turb = $('heat-turb');
  if (!turb) return;
  const intro = $('intro');
  let t = 0;
  const tick = () => {
    if (!intro.isConnected || intro.classList.contains('gone')) return;
    t += 1;
    turb.setAttribute('seed', String(2 + ((t / 7) | 0) % 64));
    const fx = 0.011 + Math.sin(t / 95) * 0.0035;
    const fy = 0.026 + Math.cos(t / 71) * 0.008;
    turb.setAttribute('baseFrequency', `${fx.toFixed(5)} ${fy.toFixed(5)}`);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
animateHeat();

main().catch(err => {
  $('story').textContent = `Could not load web/${DATA_DIR}. ${err.message}`;
  const ir = $('intro-run');
  if (ir) ir.textContent = `FAILED: ${err.message}`;
  console.error(err);
});
