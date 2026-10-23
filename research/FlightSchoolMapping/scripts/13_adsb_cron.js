#!/usr/bin/env node
/*
 * 13_adsb_cron.js — Node port of 13_adsb_cron.py (no venv to rot).
 *
 * Takes a dual-source ADS-B snapshot (OpenSky Network + adsb.lol), dedups by
 * ICAO hex, filters to US-registered aircraft (hex starting "A"), and writes a
 * timestamped JSON to data/adsb/snapshots/ plus data/adsb/latest_summary.json.
 * Output schema is byte-for-byte compatible with the Python version.
 *
 * Requires Node 18+ (global fetch). No npm deps.
 *
 * Usage:
 *   node 13_adsb_cron.js                 # one snapshot
 *   ADSB_OUT=/path/to/data/adsb node ... # override output dir
 *
 * Cron, every 15 min. Absolute paths throughout: the original Python job died
 * when a relative venv path stopped resolving, and again when the repo moved
 * from macOS to Linux. This has no venv and no relative paths.
 *
 *   [slash]15 * * * * /home/dew/.local/bin/node \
 *     /mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/scripts/13_adsb_cron.js \
 *     >> /mnt/data/home/dew/dev/aether/research/FlightSchoolMapping/data/adsb/cron.log 2>&1
 *
 * (replace [slash] with a real forward slash)
 */
'use strict';
const fs = require('fs');
const path = require('path');

// Output dir: env override, else ../data/adsb relative to this script.
const DATA_DIR = process.env.ADSB_OUT
  ? path.resolve(process.env.ADSB_OUT)
  : path.resolve(__dirname, '..', 'data', 'adsb');
const SNAPSHOTS_DIR = path.join(DATA_DIR, 'snapshots');
fs.mkdirSync(SNAPSHOTS_DIR, { recursive: true });

const OPENSKY_URL = 'https://opensky-network.org/api/states/all';
const OPENSKY_USER = process.env.OPENSKY_USER || '';
const OPENSKY_PASS = process.env.OPENSKY_PASS || '';
// Off by default: OpenSky's terms require a written agreement for automated
// REST use and forbid redistribution. adsb.lol's daily ODbL archive replaces
// this poller (docs/hackers-2026-plan.md). OPENSKY_ENABLED=1 restores it.
const OPENSKY_ENABLED = process.env.OPENSKY_ENABLED === '1';
const US_BBOX = { lamin: 24.0, lamax: 50.0, lomin: -125.0, lomax: -66.0 };

const ADSBLOL_URL = (lat, lon) => `https://api.adsb.lol/v2/lat/${lat}/lon/${lon}/dist/250`;
const ADSBLOL_GRID = [];
for (const lat of [27, 33, 39, 45]) {
  for (const lon of [-123, -115, -107, -99, -91, -83, -75, -67]) {
    ADSBLOL_GRID.push([lat, lon]);
  }
}

const OPENSKY_FIELDS = [
  'icao_hex', 'callsign', 'origin_country', 'time_position',
  'last_contact', 'longitude', 'latitude', 'altitude_m',
  'on_ground', 'velocity_ms', 'heading', 'vertical_rate',
  'sensors', 'geo_altitude_m', 'squawk', 'spi', 'category',
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const feetToMeters = (ft) => {
  if (ft === null || ft === undefined || ft === 'ground') return 0;
  const v = parseFloat(ft);
  return Number.isFinite(v) ? Math.round(v * 0.3048 * 10) / 10 : 0;
};
const knotsToMs = (kts) => {
  if (kts === null || kts === undefined) return null;
  const v = parseFloat(kts);
  return Number.isFinite(v) ? Math.round(v * 0.5144 * 10) / 10 : null;
};

async function fetchOpenSky() {
  try {
    const qs = new URLSearchParams(US_BBOX).toString();
    const headers = {};
    if (OPENSKY_USER) {
      headers['Authorization'] =
        'Basic ' + Buffer.from(`${OPENSKY_USER}:${OPENSKY_PASS}`).toString('base64');
    }
    const ctrl = AbortSignal.timeout ? AbortSignal.timeout(30000) : undefined;
    const resp = await fetch(`${OPENSKY_URL}?${qs}`, { headers, signal: ctrl });
    if (resp.status !== 200) {
      console.log(`  OpenSky: HTTP ${resp.status}`);
      return {};
    }
    const data = await resp.json();
    const ts = data.time || Math.floor(Date.now() / 1000);
    const results = {};
    for (const sv of data.states || []) {
      const rec = {};
      OPENSKY_FIELDS.forEach((field, i) => { if (i < sv.length) rec[field] = sv[i]; });
      rec.timestamp = ts;
      const hex = (rec.icao_hex || '').trim().toUpperCase();
      if (hex.startsWith('A')) {
        rec.icao_hex = hex;
        rec.callsign = (rec.callsign || '').trim();
        rec.source = 'opensky';
        results[hex] = rec;
      }
    }
    console.log(`  OpenSky: ${Object.keys(results).length} US aircraft`);
    return results;
  } catch (e) {
    console.log(`  OpenSky error: ${e.message}`);
    return {};
  }
}

async function fetchAdsbLolTile(lat, lon) {
  // adsb.lol rate-limits aggressively (429s at 5 req/s); back off and retry.
  for (let attempt = 0; attempt < 4; attempt++) {
    const ctrl = AbortSignal.timeout ? AbortSignal.timeout(15000) : undefined;
    const resp = await fetch(ADSBLOL_URL(lat, lon), {
      headers: { 'User-Agent': 'FlightSchoolResearch/1.0' },
      signal: ctrl,
    });
    if (resp.status === 429) {
      const retryAfter = parseFloat(resp.headers.get('retry-after'));
      await sleep(Number.isFinite(retryAfter) ? retryAfter * 1000 : 2000 * (attempt + 1));
      continue;
    }
    return resp;
  }
  return null;
}

async function fetchAdsbLol() {
  const results = {};
  for (const [lat, lon] of ADSBLOL_GRID) {
    try {
      const resp = await fetchAdsbLolTile(lat, lon);
      if (!resp || resp.status !== 200) {
        console.log(`  adsb.lol (${lat},${lon}): ${resp ? `HTTP ${resp.status}` : 'rate-limited after retries'}`);
        continue;
      }
      const data = await resp.json();
      for (const ac of data.ac || []) {
        const hex = (ac.hex || '').trim().toUpperCase();
        if (!hex.startsWith('A')) continue;
        if (ac.lat === null || ac.lat === undefined || ac.lon === null || ac.lon === undefined) continue;
        if (hex in results) continue; // OpenSky/first-seen wins
        results[hex] = {
          icao_hex: hex,
          callsign: (ac.flight || '').trim(),
          latitude: ac.lat,
          longitude: ac.lon,
          altitude_m: feetToMeters(ac.alt_baro),
          on_ground: ac.alt_baro === 'ground',
          velocity_ms: knotsToMs(ac.gs),
          heading: ac.track,
          registration: (ac.r || '').trim(),
          aircraft_type: (ac.t || '').trim(),
          timestamp: Math.floor(Date.now() / 1000),
          source: 'adsblol',
        };
      }
      await sleep(1000);
    } catch (e) {
      console.log(`  adsb.lol (${lat},${lon}): ${e.message}`);
    }
  }
  console.log(`  adsb.lol: ${Object.keys(results).length} US aircraft (from ${ADSBLOL_GRID.length} tiles)`);
  return results;
}

function pad(n) { return String(n).padStart(2, '0'); }

async function takeSnapshot() {
  const now = new Date();
  const iso = now.toISOString();
  console.log(`[${iso.slice(0, 19).replace('T', ' ')} UTC] Taking ADS-B snapshot...`);

  const opensky = OPENSKY_ENABLED ? await fetchOpenSky() : {};
  if (!OPENSKY_ENABLED) console.log('  OpenSky: disabled (OPENSKY_ENABLED!=1)');
  const adsblol = await fetchAdsbLol();

  const merged = { ...opensky };
  let newFromLol = 0;
  for (const [hex, rec] of Object.entries(adsblol)) {
    if (!(hex in merged)) { merged[hex] = rec; newFromLol++; }
  }
  const records = Object.values(merged);
  const onGround = records.filter((r) => r.on_ground).length;
  console.log(`  Merged: ${records.length} aircraft (${Object.keys(opensky).length} OpenSky + ${newFromLol} adsb.lol-only), ${onGround} on ground`);

  const tsStr = `${now.getUTCFullYear()}${pad(now.getUTCMonth() + 1)}${pad(now.getUTCDate())}_${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}${pad(now.getUTCSeconds())}`;
  const snapPath = path.join(SNAPSHOTS_DIR, `snapshot_${tsStr}.json`);
  fs.writeFileSync(snapPath, JSON.stringify({
    capture_utc: iso,
    total_aircraft: records.length,
    on_ground: onGround,
    sources: {
      opensky: Object.keys(opensky).length,
      adsblol: Object.keys(adsblol).length,
      adsblol_unique: newFromLol,
    },
    aircraft: records,
  }));
  const kb = Math.round(fs.statSync(snapPath).size / 1024);
  console.log(`  Saved: snapshot_${tsStr}.json (${kb} KB)`);

  fs.writeFileSync(path.join(DATA_DIR, 'latest_summary.json'), JSON.stringify({
    fetch_time: Math.floor(now.getTime() / 1000),
    total_aircraft: records.length,
    airborne: records.length - onGround,
    on_ground: onGround,
    us_registered: records.length,
    sources: { opensky: Object.keys(opensky).length, adsblol_unique: newFromLol },
  }, null, 2));

  return snapPath;
}

takeSnapshot().catch((e) => { console.error(e); process.exit(1); });
