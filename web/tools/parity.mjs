// Parity check: the browser sim (web/sim.js) vs Python (parity.json).
// Usage: node web/tools/parity.mjs [web/data ...]   (default: every town dir)
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, '..');
const { FireSim, b64ToBytes } = createRequire(import.meta.url)(join(web, 'sim.js'));

const dirs = process.argv.length > 2
  ? process.argv.slice(2)
  : JSON.parse(readFileSync(join(web, 'towns/index.json'), 'utf8')).map(t => join(web, t.data_dir));

let failed = false;
for (const dir of dirs) {
  const pPath = join(dir, 'physics.json'), qPath = join(dir, 'parity.json');
  if (!existsSync(pPath) || !existsSync(qPath)) { console.log(`${dir}: no physics/parity — skipped`); continue; }
  const physics = JSON.parse(readFileSync(pPath, 'utf8'));
  const parity = JSON.parse(readFileSync(qPath, 'utf8'));
  const sim = new FireSim(physics);
  const ign = sim.cellOf(physics.ignition_rc[0], physics.ignition_rc[1]);

  const compare = (label, got, want) => {
    let within = 0, exact = 0;
    for (let i = 0; i < got.length; i++) {
      const d = Math.abs(got[i] - want[i]);
      if (d === 0) exact++;
      if (d <= 1) within++;
    }
    const pct = (100 * within) / got.length;
    const ok = pct >= 99;
    if (!ok) failed = true;
    console.log(`${dir} ${label}: ${pct.toFixed(3)}% within 1 bucket, ${((100 * exact) / got.length).toFixed(3)}% exact — ${ok ? 'PASS' : 'FAIL'}`);
  };

  let t0 = performance.now();
  const base = sim.run(ign, null);
  const ms = performance.now() - t0;
  compare(`baseline (${ms.toFixed(0)} ms)`, sim.toBuckets(base), b64ToBytes(parity.baseline_b64));

  const mask = new Uint8Array(sim.n);
  for (const [r, c] of parity.break_cells) mask[sim.cellOf(r, c)] = 1;
  t0 = performance.now();
  const brk = sim.run(ign, mask);
  compare(`with break (${(performance.now() - t0).toFixed(0)} ms)`, sim.toBuckets(brk), b64ToBytes(parity.break_b64));

  const hb = sim.homesHit(base), hk = sim.homesHit(brk);
  console.log(`${dir} homes hit: baseline ${hb.hit}, with break ${hk.hit}; first home at ${hb.minutesToFirstHome.toFixed(1)} min`);
}
process.exit(failed ? 1 : 0);
