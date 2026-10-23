"""Nearest-airport lookup on the sphere.

Airports are stored as 3-D unit vectors, so a k-d tree's Euclidean (chord)
distance is a monotonic function of great-circle distance and the nearest
neighbour it returns is the true nearest airport. The older AirportIndex in
11_adsb_dual_source.py projects longitude with each point's own latitude and
compares in a flat plane, which picks the wrong airport near others.

Heliports, seaplane bases and balloonports are included: training and
rotorcraft operations use them. Closed airports are excluded.
"""

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

EARTH_RADIUS_NM = 3440.065
DEFAULT_CSV = Path(__file__).resolve().parent.parent / "data" / "ourairports_airports.csv"
# Bump when matching rules change; 32_reassign_airports.py brings old days up to date.
AIRPORT_INDEX_VERSION = 2

# Lower wins when candidates are within the ambiguity window of each other.
TYPE_RANK = {"large_airport": 0, "medium_airport": 0, "small_airport": 0,
             "seaplane_base": 1, "balloonport": 2, "heliport": 3}
INCLUDED_TYPES = {
    "large_airport", "medium_airport", "small_airport",
    "heliport", "seaplane_base", "balloonport",
}


def _unit(lat_deg, lon_deg):
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    return np.column_stack((np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)))


def chord_to_nm(chord):
    """Convert unit-sphere chord length to great-circle distance in nm."""
    return 2.0 * np.arcsin(np.clip(np.asarray(chord) / 2.0, 0.0, 1.0)) * EARTH_RADIUS_NM


def haversine_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(a))


@dataclass
class Match:
    ident: str          # gps_code / local_code / ident, whichever exists first
    name: str
    type: str
    distance_nm: float
    ambiguous: bool     # a second airport is almost as close


class AirportIndex:
    def __init__(self, csv_path=DEFAULT_CSV):
        idents, names, types, lats, lons = [], [], [], [], []
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["type"] not in INCLUDED_TYPES:
                    continue
                try:
                    lat, lon = float(row["latitude_deg"]), float(row["longitude_deg"])
                except ValueError:
                    continue
                idents.append(row["gps_code"] or row["local_code"] or row["ident"])
                names.append(row["name"])
                types.append(row["type"])
                lats.append(lat)
                lons.append(lon)
        self.idents = idents
        self.names = names
        self.types = types
        self.lats = np.array(lats)
        self.lons = np.array(lons)
        self.tree = cKDTree(_unit(self.lats, self.lons))

    def nearest(self, lat, lon, max_nm=3.0, ambiguity_nm=0.5):
        """Best airport within max_nm, or None.

        Among candidates within ambiguity_nm of the nearest, a runway airport
        beats a heliport, seaplane base or balloonport: a Cessna doing
        touch-and-goes at Schaumburg (06C) is otherwise matched to Clarke
        Heliport (LL95), 0.2 nm away. ambiguous=True when another candidate of
        the same rank is also inside that window.
        """
        dist, idx = self.tree.query(_unit([lat], [lon])[0], k=8)
        nm = chord_to_nm(dist)
        if nm[0] > max_nm:
            return None
        window = [(float(d), int(i)) for d, i in zip(nm, idx) if d - nm[0] < ambiguity_nm and d <= max_nm]
        best_rank = min(TYPE_RANK.get(self.types[i], 9) for _, i in window)
        top = [(d, i) for d, i in window if TYPE_RANK.get(self.types[i], 9) == best_rank]
        d, i = top[0]
        return Match(
            ident=self.idents[i],
            name=self.names[i],
            type=self.types[i],
            distance_nm=d,
            ambiguous=len(top) > 1,
        )
