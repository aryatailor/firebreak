/* Firebreak — minimal skeleton. Arya: this file is yours; DESIGN.md is the real spec.
   This exists only to prove the data contract (CONTRACT.md) decodes and aligns on the
   map. Replace it freely. Switch to real pipeline output: DATA_DIR = 'data'. */

const DATA_DIR = 'mock';
const UNREACHED = 65535;

async function loadJSON(name) {
  const r = await fetch(`${DATA_DIR}/${name}`);
  if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
  return r.json();
}

// CONTRACT.md: base64 of little-endian uint16, row-major, top row = north.
function decodeGrid(b64, rows, cols) {
  const bin = atob(b64);
  if (bin.length !== rows * cols * 2) {
    throw new Error(`grid is ${bin.length} bytes, expected ${rows * cols * 2}`);
  }
  const bytes = Uint8Array.from(bin, ch => ch.charCodeAt(0));
  const dv = new DataView(bytes.buffer);
  const out = new Uint16Array(rows * cols);
  for (let i = 0; i < out.length; i++) out[i] = dv.getUint16(i * 2, true);
  return out;
}

// Fire age ramp (DESIGN.md): just-ignited #ffd166 → #ff7a1a → #c1121f → #2b2b2b
const RAMP = [[0xff, 0xd1, 0x66], [0xff, 0x7a, 0x1a], [0xc1, 0x12, 0x1f], [0x2b, 0x2b, 0x2b]];
function rampColor(ageMin) {
  const f = Math.min(ageMin / 180, 1) * (RAMP.length - 1);
  const i = Math.min(Math.floor(f), RAMP.length - 2);
  const t = f - i;
  return [0, 1, 2].map(k => Math.round(RAMP[i][k] + (RAMP[i + 1][k] - RAMP[i][k]) * t));
}

async function main() {
  const meta = await loadJSON('meta.json');
  const [baseline, solutions, curve, buildingsFC] = await Promise.all([
    loadJSON('baseline.json'), loadJSON('solutions.json'),
    loadJSON('curve.json'), loadJSON('buildings.geojson'),
  ]);
  // curve.json feeds the panel's SVG chart (DESIGN.md) — not drawn in this skeleton.
  console.log(`curve.json: ${curve.points.length} greedy steps loaded`);

  const { rows, cols } = meta.grid;
  const b = meta.bounds;
  const bounds = L.latLngBounds([b.south, b.west], [b.north, b.east]);

  const map = L.map('map', { zoomSnap: 0.25 });
  map.fitBounds(bounds);
  // Online basemap. Offline, tiles just never load and basemap.png below covers the
  // area of interest. DESIGN.md specifies the real 3 s auto-detect fallback.
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    { maxZoom: 17, attribution: 'Imagery © Esri' }).addTo(map);
  L.imageOverlay(`${DATA_DIR}/basemap.png`, bounds, { opacity: 0.85 }).addTo(map);

  // Fire layer: render the arrival grid to a canvas (one pixel per cell), show it as
  // an image overlay placed with meta.bounds. Row 0 = north = canvas y 0, so
  // row-major ImageData maps 1:1 with no flipping.
  const canvas = document.createElement('canvas');
  canvas.width = cols;
  canvas.height = rows;
  const ctx = canvas.getContext('2d');
  const fire = L.imageOverlay(canvas.toDataURL(), bounds,
    { opacity: 0.75, className: 'fire-overlay' }).addTo(map);

  L.marker([meta.ignition.lat, meta.ignition.lon]).addTo(map)
    .bindTooltip(meta.ignition.label);

  const dots = buildingsFC.features.map(f => {
    const [lon, lat] = f.geometry.coordinates;
    const m = L.circleMarker([lat, lon],
      { radius: 2, stroke: false, fillColor: '#ffffff', fillOpacity: 0.4 }).addTo(map);
    return { m, cell: f.properties.row * cols + f.properties.col, hit: false };
  });

  const state = {
    arrival: decodeGrid(baseline.arrival_min_b64, rows, cols),
    sol: null,               // null = baseline ($0)
    t: meta.horizon_min,
  };
  let breaksLayer = null;

  function renderFire() {
    const img = ctx.createImageData(cols, rows);
    const d = img.data, a = state.arrival, t = state.t;
    for (let i = 0; i < a.length; i++) {
      if (a[i] === UNREACHED || a[i] > t) continue;   // unburned: transparent
      const [r, g, bl] = rampColor(t - a[i]);
      const o = i * 4;
      d[o] = r; d[o + 1] = g; d[o + 2] = bl; d[o + 3] = 255; // layer opacity = 0.75
    }
    ctx.putImageData(img, 0, 0);
    fire.setUrl(canvas.toDataURL());
  }

  function renderBuildings() {
    let hit = 0;
    for (const dot of dots) {
      const a = state.arrival[dot.cell];
      const isHit = a !== UNREACHED && a <= state.t;
      if (isHit !== dot.hit) {
        dot.hit = isHit;
        dot.m.setStyle(isHit ? { fillColor: '#ff3b3b', fillOpacity: 0.9 }
                             : { fillColor: '#ffffff', fillOpacity: 0.4 });
      }
      if (isHit) hit++;
    }
    document.getElementById('stat-hit').textContent = `${hit} / ${dots.length}`;
  }

  function setSolution(sol) {   // sol = entry of solutions.json, or null for baseline
    state.sol = sol;
    state.arrival = decodeGrid((sol || baseline).arrival_min_b64, rows, cols);
    if (breaksLayer) { map.removeLayer(breaksLayer); breaksLayer = null; }
    if (sol) {
      breaksLayer = L.geoJSON(sol.breaks, {
        style: { color: '#ffffff', weight: 1, fillColor: '#3ddc84', fillOpacity: 0.8 },
      }).addTo(map);
    }
    document.getElementById('stat-spent').textContent =
      sol ? `$${sol.cost.toLocaleString()}` : '$0';
    document.getElementById('stat-saved').textContent =
      sol ? String(sol.stats.houses_saved) : '0';
    renderFire();
    renderBuildings();
  }

  // Controls (bare — DESIGN.md replaces all of this)
  const sel = document.getElementById('budget');
  sel.append(new Option('$0 (baseline)', ''));
  solutions.forEach((s, i) => sel.append(new Option(`$${s.budget.toLocaleString()}`, String(i))));
  sel.onchange = () => setSolution(sel.value === '' ? null : solutions[Number(sel.value)]);

  const time = document.getElementById('time');
  const timeLabel = document.getElementById('time-label');
  time.max = String(meta.horizon_min);
  time.value = String(meta.horizon_min);
  time.oninput = () => {
    state.t = Number(time.value);
    timeLabel.textContent = `${state.t} min`;
    renderFire();
    renderBuildings();
  };

  const playBtn = document.getElementById('play');
  let timer = null;
  playBtn.onclick = () => {
    if (timer) { clearInterval(timer); timer = null; playBtn.textContent = 'Play'; return; }
    if (Number(time.value) >= meta.horizon_min) time.value = '0';
    playBtn.textContent = 'Pause';
    // 5-minute steps every 140 ms ≈ 20 s for a full 0→720 sweep (DESIGN.md)
    timer = setInterval(() => {
      time.value = String(Math.min(Number(time.value) + 5, meta.horizon_min));
      time.oninput();
      if (Number(time.value) >= meta.horizon_min) {
        clearInterval(timer); timer = null; playBtn.textContent = 'Play';
      }
    }, 140);
  };

  document.getElementById('story').textContent = meta.story;
  document.getElementById('data-dir').textContent = `web/${DATA_DIR}/`;
  timeLabel.textContent = `${state.t} min`;
  setSolution(null);
}

main().catch(err => {
  document.getElementById('story').textContent = `FAILED: ${err.message}`;
  console.error(err);
});
