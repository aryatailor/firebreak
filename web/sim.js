/* Firebreak — browser fire simulation. Reimplements CONTRACT.md "The algorithm,
   exactly" from physics.json: Dijkstra over a directed 8-neighbour grid graph,
   edge weight = dist / (min(base_i, base_j) * f_slope * f_wind * scale).
   Plain script (no modules) so it runs from file:// and python -m http.server;
   also loadable from Node for the parity check (see tools/parity.mjs). */
(function (root) {
  'use strict';

  const NEIGH = [[-1, -1], [-1, 0], [-1, 1], [0, -1], [0, 1], [1, -1], [1, 0], [1, 1]];

  function b64ToBytes(b64) {
    if (typeof atob === 'function') {
      const bin = atob(b64);
      const out = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
      return out;
    }
    return new Uint8Array(Buffer.from(b64, 'base64'));
  }

  /* Binary min-heap keyed on a Float64Array of distances (decrease-key by
     lazy re-insertion; stale entries are skipped on pop). */
  class Heap {
    constructor(cap) {
      this.keys = new Float64Array(cap);
      this.vals = new Int32Array(cap);
      this.n = 0;
    }
    push(k, v) {
      if (this.n === this.keys.length) {
        const nk = new Float64Array(this.n * 2), nv = new Int32Array(this.n * 2);
        nk.set(this.keys); nv.set(this.vals);
        this.keys = nk; this.vals = nv;
      }
      let i = this.n++;
      const keys = this.keys, vals = this.vals;
      while (i > 0) {
        const p = (i - 1) >> 1;
        if (keys[p] <= k) break;
        keys[i] = keys[p]; vals[i] = vals[p];
        i = p;
      }
      keys[i] = k; vals[i] = v;
    }
    pop() {
      const keys = this.keys, vals = this.vals;
      const topV = vals[0];
      const n = --this.n;
      if (n > 0) {
        const k = keys[n], v = vals[n];
        let i = 0;
        for (;;) {
          let c = 2 * i + 1;
          if (c >= n) break;
          if (c + 1 < n && keys[c + 1] < keys[c]) c++;
          if (keys[c] >= k) break;
          keys[i] = keys[c]; vals[i] = vals[c];
          i = c;
        }
        keys[i] = k; vals[i] = v;
      }
      return topV;
    }
  }

  class FireSim {
    constructor(physics) {
      const g = physics.grid;
      this.rows = g.rows; this.cols = g.cols; this.cellM = g.cell_m;
      this.n = this.rows * this.cols;
      this.fuel = b64ToBytes(physics.fuel_class_b64);
      const eb = b64ToBytes(physics.elev_m_b64);
      this.elev = new Int16Array(eb.buffer, eb.byteOffset, this.n);
      this.rates = physics.rates;
      this.scale = physics.scale_m_per_min;
      this.kW = physics.k_w;
      this.breakMult = physics.break_mult;
      this.horizon = physics.horizon_min;
      this.bucketMin = physics.bucket_min || 5;
      this.homesRc = physics.homes_rc;
      this.ignitionRc = physics.ignition_rc;
      this.wind = { speed_mph: physics.wind.speed_mph, from_deg: physics.wind.from_deg };
      this.base = new Float64Array(this.n);
      for (let i = 0; i < this.n; i++) {
        const f = this.fuel[i];
        this.base[i] = f === 0 ? 0 : (this.rates[String(f)] || 0);
      }
      // Per-direction constants that do not depend on wind.
      this.dist = NEIGH.map(([dr, dc]) => this.cellM * Math.hypot(dr, dc));
      this.setWind(this.wind.speed_mph, this.wind.from_deg);
      this._heap = new Heap(this.n * 2);
    }

    setWind(speedMph, fromDeg) {
      this.wind = { speed_mph: speedMph, from_deg: fromDeg };
      const down = (((fromDeg + 180) % 360) + 360) % 360 * Math.PI / 180;
      this.fWind = NEIGH.map(([dr, dc]) => {
        const bearing = Math.atan2(dc, -dr);
        return Math.exp(this.kW * (speedMph / 10) * Math.cos(bearing - down));
      });
    }

    cellOf(row, col) { return row * this.cols + col; }

    /* breakMask: Uint8Array(n) (1 = cleared) or null. Returns Float64Array
       arrival minutes; Infinity where unreached. */
    run(ignitionCell, breakMask) {
      const { rows, cols, n, base, elev, dist, fWind, scale, breakMult } = this;
      const arrival = new Float64Array(n).fill(Infinity);
      if (ignitionCell < 0 || ignitionCell >= n) return arrival;
      const eff = breakMask
        ? base.map((b, i) => (breakMask[i] ? b * breakMult : b))
        : base;
      const heap = this._heap; heap.n = 0;
      arrival[ignitionCell] = 0;
      heap.push(0, ignitionCell);
      const done = new Uint8Array(n);
      while (heap.n > 0) {
        const i = heap.pop();
        if (done[i]) continue;
        done[i] = 1;
        const di = arrival[i];
        const bi = eff[i];
        if (bi === 0) continue;
        const r = (i / cols) | 0, c = i - r * cols;
        for (let k = 0; k < 8; k++) {
          const dr = NEIGH[k][0], dc = NEIGH[k][1];
          const rr = r + dr, cc = c + dc;
          if (rr < 0 || rr >= rows || cc < 0 || cc >= cols) continue;
          const j = rr * cols + cc;
          if (done[j]) continue;
          const bj = eff[j];
          if (bj === 0) continue;
          const bmin = bi < bj ? bi : bj;
          const d = dist[k];
          const theta = Math.atan((elev[j] - elev[i]) / d) * 180 / Math.PI;
          let fs = Math.pow(2, theta / 10);
          if (fs < 0.5) fs = 0.5; else if (fs > 8) fs = 8;
          const w = d / (bmin * fs * fWind[k] * scale);
          const nd = di + w;
          if (nd < arrival[j]) { arrival[j] = nd; heap.push(nd, j); }
        }
      }
      return arrival;
    }

    /* uint16 grid in the baseline.json convention (65535 = never / beyond horizon). */
    toU16(arrival) {
      const out = new Uint16Array(this.n);
      for (let i = 0; i < this.n; i++) {
        const a = arrival[i];
        out[i] = a <= this.horizon ? Math.min(65534, Math.round(a)) : 65535;
      }
      return out;
    }

    /* uint8 5-minute buckets (steps.json / parity.json convention, 255 = never). */
    toBuckets(arrival) {
      const out = new Uint8Array(this.n);
      for (let i = 0; i < this.n; i++) {
        const a = arrival[i];
        out[i] = a <= this.horizon ? Math.min(254, Math.round(a / this.bucketMin)) : 255;
      }
      return out;
    }

    homesHit(arrival) {
      let hit = 0, first = Infinity;
      for (const [r, c] of this.homesRc) {
        const a = arrival[r * this.cols + c];
        if (a <= this.horizon) { hit++; if (a < first) first = a; }
      }
      return { hit, minutesToFirstHome: first };
    }
  }

  root.FireSim = FireSim;
  root.FireSim.b64ToBytes = b64ToBytes;
  if (typeof module !== 'undefined' && module.exports) module.exports = { FireSim, b64ToBytes };
})(typeof window !== 'undefined' ? window : globalThis);
