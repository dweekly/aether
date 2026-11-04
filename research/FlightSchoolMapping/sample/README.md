# Sample data for `make sample`

A small, licensed slice of the real inputs, so a fresh clone can run the ingest
(`scripts/30_adsblol_history.py --traces`) and the registry join
(`scripts/34_fleet_summary.py`) in a minute and check a known answer.

- `traces/<xx>/trace_full_<hex>.json`: the 101 aircraft whose last ground position on
  2026-10-22 was KPAO, KSQL, KRHV or KHWD, unchanged from adsb.lol's
  `v2026.10.22-planes-readsb-prod-0` release (gzip JSON, readsb trace format).
  Contains information from adsb.lol, made available under the
  [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
- `registry/2026-10-23/`: the FAA Releasable Aircraft Registry snapshot of 2026-10-23
  (public domain), cut to the 101 aircraft above plus every aircraft registered in
  Palo Alto or San Carlos: 317 rows of `master.parquet` and the 150 `acftref.parquet`
  rows they reference.
- `expected.json`: the day list and classification totals `make sample` must reproduce.
