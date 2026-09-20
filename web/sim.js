/* sim.js — the fire model in the browser, per CONTRACT.md "The algorithm, exactly".
   Runs in a Web Worker so the UI never freezes. Dijkstra over a directed 8-neighbour
   graph, binary heap, edge weights computed on the fly, arrival in 5-minute buckets.

   Messages in:  {type:'load', dataDir}
                 {type:'run', id, ignition_rc, wind, breaks}      breaks = cell indices
   Messages out: {type:'ready', rows, cols, ...} | {type:'result', ...} | {type:'error'} */

let P = null;                 // parsed physics.json
let fuel = null, elev = null; // Uint8Array, Int16Array
let base0 = null;             // Float32Array, base rate per cell before breaks
let rows = 0, cols = 0, n = 0;

const DR = [-1, -1, -1, 0, 0, 1, 1, 1];
const DC = [-1, 0, 1, -1, 1, -1, 0, 1];

function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/* Wind factor depends only on travel direction, so it is 8 constants:
   bearing = atan2(dc, -dr); f = exp(k_w * (mph/10) * cos(bearing - downwind)). */
function windFactors(wind) {
  const downwind = ((wind.from_deg + 180) % 360) * Math.PI / 180;
  const k = P.k_w * (wind.speed_mph / 10);
  const f = new Float64Array(8);
  for (let d = 0; d < 8; d++) {
    const bearing = Math.atan2(DC[d], -DR[d]);
    f[d] = Math.exp(k * Math.cos(bearing - downwind));
  }
  return f;
}

function run(ignition_rc, wind, breaks) {
  const t0 = Date.now();
  const cell_m = P.grid.cell_m;
  const scale = P.scale_m_per_min;
  const horizon = P.horizon_min;
  const bucket = P.bucket_min;

  // base rates, with any cleared cells scaled by break_mult (both directions,
  // because every edge uses min(base_i, base_j))
  const base = base0.slice();
  if (breaks && breaks.length) {
    const bm = P.break_mult;
    for (let i = 0; i < breaks.length; i++) base[breaks[i]] *= bm;
  }

  const wf = windFactors(wind || P.wind);
  const distOf = new Float64Array(8);
  for (let d = 0; d < 8; d++) distOf[d] = cell_m * Math.hypot(DR[d], DC[d]);

  const INF = Infinity;
  const dist = new Float64Array(n).fill(INF);
  const done = new Uint8Array(n);

  // binary min-heap with lazy deletion
  let cap = 1 << 16;
  let hk = new Float64Array(cap);   // keys
  let hv = new Int32Array(cap);     // cell ids
  let hn = 0;
  const push = (key, val) => {
    if (hn === cap) {
      cap <<= 1;
      const nk = new Float64Array(cap); nk.set(hk); hk = nk;
      const nv = new Int32Array(cap); nv.set(hv); hv = nv;
    }
    let i = hn++;
    hk[i] = key; hv[i] = val;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (hk[p] <= hk[i]) break;
      const tk = hk[p]; hk[p] = hk[i]; hk[i] = tk;
      const tv = hv[p]; hv[p] = hv[i]; hv[i] = tv;
      i = p;
    }
  };
  const pop = () => {
    const top = hv[0];
    hn--;
    if (hn > 0) {
      hk[0] = hk[hn]; hv[0] = hv[hn];
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let s = i;
        if (l < hn && hk[l] < hk[s]) s = l;
        if (r < hn && hk[r] < hk[s]) s = r;
        if (s === i) break;
        const tk = hk[s]; hk[s] = hk[i]; hk[i] = tk;
        const tv = hv[s]; hv[s] = hv[i]; hv[i] = tv;
        i = s;
      }
    }
    return top;
  };

  const src = ignition_rc[0] * cols + ignition_rc[1];
  dist[src] = 0;
  push(0, src);

  while (hn > 0) {
    const u = pop();
    if (done[u]) continue;
    done[u] = 1;
    const du = dist[u];
    if (du > horizon) break;             // nothing beyond the horizon matters
    const bu = base[u];
    if (bu <= 0) continue;
    const ur = (u / cols) | 0, uc = u - ur * cols;
    const eu = elev[u];
    for (let d = 0; d < 8; d++) {
      const vr = ur + DR[d], vc = uc + DC[d];
      if (vr < 0 || vr >= rows || vc < 0 || vc >= cols) continue;
      const v = vr * cols + vc;
      if (done[v]) continue;
      const bv = base[v];
      if (bv <= 0) continue;
      const bmin = bu < bv ? bu : bv;
      const dm = distOf[d];
      // slope: theta = atan(dz / dist), f = 2^(theta_deg/10) clamped to [0.5, 8]
      const theta = Math.atan((elev[v] - eu) / dm) * 180 / Math.PI;
      let fs = Math.pow(2, theta / 10);
      if (fs < 0.5) fs = 0.5; else if (fs > 8) fs = 8;
      const w = dm / (bmin * fs * wf[d] * scale);
      const nd = du + w;
      if (nd < dist[v]) { dist[v] = nd; push(nd, v); }
    }
  }

  // bucket encoding: round(minutes / bucket_min), 255 past the horizon
  const out = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const m = dist[i];
    out[i] = (m > horizon || !isFinite(m)) ? 255 : Math.min(254, Math.round(m / bucket));
  }

  // homes reached within the horizon
  let hit = 0, firstHome = Infinity;
  const hr = P.homes_rc;
  for (let i = 0; i < hr.length; i++) {
    const m = dist[hr[i][0] * cols + hr[i][1]];
    if (m <= horizon) hit++;
    if (m < firstHome) firstHome = m;
  }

  return {
    buckets: out,
    stats: {
      homes_total: hr.length, homes_hit: hit,
      minutes_to_first_home: isFinite(firstHome) ? +firstHome.toFixed(1) : null,
      sim_seconds: +((Date.now() - t0) / 1000).toFixed(3),
    },
  };
}

self.onmessage = async e => {
  const msg = e.data;
  try {
    if (msg.type === 'load') {
      const r = await fetch(`${msg.dataDir}/physics.json`);
      if (!r.ok) throw new Error(`physics.json: HTTP ${r.status}`);
      P = await r.json();
      rows = P.grid.rows; cols = P.grid.cols; n = rows * cols;
      fuel = b64ToBytes(P.fuel_class_b64);
      const eb = b64ToBytes(P.elev_m_b64);
      elev = new Int16Array(eb.buffer, eb.byteOffset, n);
      base0 = new Float32Array(n);
      for (let i = 0; i < n; i++) base0[i] = P.rates[fuel[i]] || 0;   // class 0 = 0
      self.postMessage({
        type: 'ready', rows, cols, cell_m: P.grid.cell_m,
        bucket_min: P.bucket_min, horizon_min: P.horizon_min,
        ignition_rc: P.ignition_rc, wind: P.wind,
        cost_per_acre: P.cost_per_acre, cell_acres: P.cell_acres,
        fuel: fuel.slice(),
      });
      return;
    }
    if (msg.type === 'run') {
      if (!P) throw new Error('sim not loaded');
      const res = run(msg.ignition_rc || P.ignition_rc, msg.wind, msg.breaks);
      self.postMessage({ type: 'result', id: msg.id, buckets: res.buckets, stats: res.stats },
        [res.buckets.buffer]);
      return;
    }
    if (msg.type === 'parity') {
      if (!P) throw new Error('sim not loaded');
      const r = await fetch(`${msg.dataDir}/parity.json`);
      if (!r.ok) throw new Error(`parity.json: HTTP ${r.status}`);
      const par = await r.json();
      const report = [];
      const cases = [
        ['baseline', b64ToBytes(par.baseline_b64), []],
        ['break', b64ToBytes(par.break_b64),
          (par.break_cells || []).map(rc => rc[0] * cols + rc[1])],
      ];
      for (const [name, want, breaks] of cases) {
        const got = run(P.ignition_rc, P.wind, breaks).buckets;
        let within1 = 0, exact = 0, worst = 0;
        for (let i = 0; i < n; i++) {
          const a = got[i], bwant = want[i];
          const d = a === bwant ? 0 : (a === 255 || bwant === 255) ? 255 : Math.abs(a - bwant);
          if (d === 0) exact++;
          if (d <= 1) within1++;
          if (d !== 255 && d > worst) worst = d;
        }
        report.push({
          case: name, cells: n,
          pct_within_1: +(100 * within1 / n).toFixed(3),
          pct_exact: +(100 * exact / n).toFixed(3),
          worst_bucket_delta: worst,
        });
      }
      self.postMessage({ type: 'parity', report });
      return;
    }
  } catch (err) {
    self.postMessage({ type: 'error', where: msg && msg.type, message: String(err && err.message || err) });
  }
};
