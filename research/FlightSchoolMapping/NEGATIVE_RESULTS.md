# Negative results

Things we tried that did not work, so nobody re-runs them expecting otherwise or
presents the opposite as a finding.

## Day-of-week does not predict when a GA aircraft flies (2026-10-23)

`41_privacy.py`, window 2026-09-15 → 2026-10-22 (38 days), 53,603 aircraft with
≥ 8 flying days. Learning P(flies | weekday) from the first half and predicting
each day of the second half scored 62.9% against 66.1% for a baseline that
always predicts the aircraft's majority (fly / don't fly). The weekly schedule
is not a useful handle on an individual GA aircraft; any privacy claim about
"predictable routines" needs a different feature (time of day, route) and its
own test.

## Logistic regression on flight behavior alone finds few training aircraft (2026-10-23)

`40_training_detect.py`, first version: seven behavior features (same-airport
share, short legs, low altitude, legs per day, days flown, airports, weekday
share) in a cross-validated logistic regression reached 19% recall at a 23%
precision floor. Gradient-boosted trees with two priors (how common the
make/model is on known school lines, how many active aircraft the registrant
holds) reached average precision ≥ 0.40 against an 8% base rate. Most of that
lift comes from the priors, not the behavior; say so when presenting it.

## The 15-minute live poller undercounted and was rate-limited (2026-10-23)

The 96 daily snapshots of 2026-10-22 saw 33,736 US addresses; adsb.lol's
archive for the same day has 38,139 (98.6% of the snapshot set plus 4,880
more). From about 2026-10-17 the poller was also exceeding adsb.lol's 500
requests/day per-IP cap. Polling is retired; the archive replaces it.
