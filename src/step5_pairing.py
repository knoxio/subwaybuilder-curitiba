"""Step 5 — pair homes to workplaces with a calibrated doubly-constrained gravity model.

A naive pairing (nearest job, or uniform random) produces a commute-distance histogram the
simulation's pathfinding treats very differently from a real one, so the deterrence function is
fitted to observed data rather than assumed.

**The target.** Censo 2022 table 10330 crossed by mode gives the commute-time distribution *for
car commuters only*. That is the right target because the modelled cost here is a car travel
time; the all-mode distribution is materially slower (10.0% of all Curitiba commutes take 1-2 h
against 4.2% of car commutes) and calibrating car times against it would stretch every trip.

**The model.** `w_ij = jobs_j * f(t_ij)` with a combined deterrence `f(t) = t^-alpha * exp(-beta t)`,
fitted by grid search to minimise chi-square against the seven observed time bands. The two
parameters matter: a pure exponential cannot hold both the short-trip peak and the long tail.

**Balancing.** Iterative proportional fitting on the sparse top-K candidate matrix until row sums
match residents and column sums match jobs. Then flows are converted to integer pops, conserving
the total exactly.

    python3 src/step5_pairing.py [--top-k 14] [--min-flow 8]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request

import cwb
import numpy as np
from sources.fetch import ssl_context

MAXPOPSIZE = 200
EARTH_KM = 6371.0088

# Straight-line to road distance. 1.35 is the usual planar detour factor for a gridded city.
DETOUR = 1.35
# Door-to-door overhead: reaching the car, parking, walking the last stretch. The census question
# asks about the whole journey, so a pure link time would read short.
TERMINAL_MIN = 4.0
# Speed rises with trip length as more of it runs on arterials: v(d) = V_MIN + (V_MAX-V_MIN)*d/(d+D_HALF)
V_MIN, V_MAX, D_HALF = 16.0, 52.0, 6.0

CAR_MODE_CATEGORY = "79197"


def fetch_car_time_target(refresh: bool = False) -> tuple[list[float], int]:
    """Observed car-commute time distribution, summed over the extent's municipalities."""
    counts = [0] * len(cwb.TIME_BANDS)
    total = 0
    for code, name in cwb.MUNICIPALITIES:
        cache = cwb.RAW / "sidra" / f"commute_time_car_{code}.json"
        if cache.exists() and not refresh:
            payload = cwb.read_json(cache)
        else:
            url = (
                f"{cwb.SIDRA}/{cwb.COMMUTE_TABLE}/periodos/{cwb.COMMUTE_YEAR}"
                f"/variaveis/{cwb.COMMUTE_VAR}?localidades=N6%5B{code}%5D"
                f"&classificacao={cwb.CLS_TIME}%5Ball%5D"
                f"%7C{cwb.CLS_MODE}%5B{CAR_MODE_CATEGORY}%5D"
                f"%7C{cwb.CLS_RACE}%5B{cwb.CAT_TOTAL[cwb.CLS_RACE]}%5D"
                f"%7C{cwb.CLS_WORKPLACE}%5B{cwb.CAT_TOTAL[cwb.CLS_WORKPLACE]}%5D"
            )
            payload = cwb.http_json(url)
            cache.parent.mkdir(parents=True, exist_ok=True)
            with cache.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)

        by_band = {}
        for variable in payload if isinstance(payload, list) else []:
            for block in variable.get("resultados", []):
                band = None
                for classification in block.get("classificacoes", []):
                    if classification.get("id") == cwb.CLS_TIME:
                        band = next(iter(classification["categoria"]))
                for series in block.get("series", []):
                    by_band[band] = cwb.sidra_value(next(iter(series["serie"].values()))) or 0
        for index, (band_id, _label, _lo, _hi) in enumerate(cwb.TIME_BANDS):
            counts[index] += by_band.get(band_id, 0)
        total += by_band.get(cwb.CAT_TOTAL[cwb.CLS_TIME], 0)

    observed = sum(counts)
    shares = [c / observed for c in counts] if observed else []
    return shares, total


def band_shares(times: np.ndarray, weights: np.ndarray) -> list[float]:
    """Weighted share of trips falling in each census time band."""
    total = weights.sum()
    if total <= 0:
        return [0.0] * len(cwb.TIME_BANDS)
    out = []
    for _id, _label, lo, hi in cwb.TIME_BANDS:
        mask = (times > lo) & (times <= hi) if lo > 0 else (times <= hi)
        out.append(float(weights[mask].sum() / total))
    return out


def travel_minutes(
    km: np.ndarray, terminal: float = TERMINAL_MIN, v_max: float = V_MAX, v_min: float = V_MIN
) -> np.ndarray:
    """Door-to-door car minutes for a straight-line distance in km.

    `terminal` and `v_max` are fitted rather than assumed, because both are strongly identified
    by the observed distribution and getting them wrong distorts the tails in opposite
    directions. A large terminal time swallows the whole "up to 5 min" band — with 4 minutes of
    overhead a trip must be under one minute of driving to land there, which is why an early run
    produced 0.35% against an observed 6.25%. A high free-flow `v_max` compresses long trips out
    of the "1-2 h" band, which no free-flow speed can reach across a 60 km metro.
    """
    road = km * DETOUR
    speed = v_min + (v_max - v_min) * road / (road + D_HALF)
    return terminal + road / speed * 60.0


def deterrence(minutes: np.ndarray, alpha: float, beta: float, floor: float) -> np.ndarray:
    """Combined power-exponential deterrence with a flat floor below `floor` minutes.

    The floor is the important part. Establishments are everywhere — 148,690 job sites across
    this extent — so any monotonically decaying function pairs most people with a job a few
    hundred metres away, and the modelled distribution collapses onto the shortest bands. The
    census says only 6.25% of car commutes take under five minutes, so that is wrong: people
    demonstrably do not take the nearest available job. Holding the weight constant below a
    threshold removes the artificial preference for the closest job without otherwise changing
    the shape. The Dubai map documents the same failure and the same fix.
    """
    effective = np.maximum(minutes, floor)
    return np.power(effective, -alpha) * np.exp(-beta * effective)


def haversine_matrix(o_lat, o_lon, d_lat, d_lon) -> np.ndarray:
    """Great-circle km between every origin row and every destination column."""
    dlat = d_lat[None, :] - o_lat[:, None]
    dlon = d_lon[None, :] - o_lon[:, None]
    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(o_lat)[:, None] * np.cos(d_lat)[None, :] * np.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def largest_remainder_float(total: int, shares: list[float]) -> list[int]:
    """Split `total` across fractional `shares` so the parts are integers summing to `total`."""
    if total <= 0 or not shares:
        return [0] * len(shares)
    scale = sum(shares) or 1.0
    exact = [total * s / scale for s in shares]
    parts = [int(v) for v in exact]
    shortfall = total - sum(parts)
    if shortfall > 0:
        order = sorted(range(len(shares)), key=lambda i: exact[i] - parts[i], reverse=True)
        for i in order[:shortfall]:
            parts[i] += 1
    return parts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--min-flow", type=int, default=25, help="minimum people per pop")
    parser.add_argument("--ipf-iters", type=int, default=6)
    parser.add_argument("--chunk", type=int, default=768)
    args = parser.parse_args()

    cwb.banner("Step 5 — gravity pairing")

    points = cwb.read_json(cwb.INTERIM / "points.json")
    origins = [p for p in points if p["residents"] > 0]
    dests = [p for p in points if p["jobs"] > 0]
    print(f"  points {len(points):,}  origins {len(origins):,}  destinations {len(dests):,}")

    o_res = np.array([p["residents"] for p in origins], dtype=np.float64)
    d_job = np.array([p["jobs"] for p in dests], dtype=np.float64)
    o_lat = np.radians(np.array([p["location"][1] for p in origins]))
    o_lon = np.radians(np.array([p["location"][0] for p in origins]))
    d_lat = np.radians(np.array([p["location"][1] for p in dests]))
    d_lon = np.radians(np.array([p["location"][0] for p in dests]))
    print(f"  residents {o_res.sum():,.0f}  jobs {d_job.sum():,.0f}")

    target, reported_total = fetch_car_time_target()
    print()
    print(f"  observed car commuters in extent: {reported_total:,}")
    print("  target car-commute time distribution:")
    for (_, label, _, _), share in zip(cwb.TIME_BANDS, target):
        print(f"    {label:<14} {share * 100:5.2f}%")

    # ---- calibrate on time-binned job histograms ----
    # The deterrence depends only on travel time, and only the band distribution is being fitted,
    # so the full origin x destination matrix is unnecessary: collapse each origin's destinations
    # into 1-minute job histograms once, then every candidate parameter set is a cheap operation
    # on a sample x bins array. Evaluating the grid directly on the full matrix would be tens of
    # billions of operations per sweep; this is a few hundred thousand.
    rng = np.random.default_rng(20260731)
    sample_n = min(3000, len(origins))
    probability = o_res / o_res.sum()
    sample = rng.choice(len(origins), size=sample_n, replace=False, p=probability)
    s_res = o_res[sample]

    # Histogram by *distance*, not time, so the speed model can be varied without rebuilding it.
    km_edges = np.arange(0, 120.25, 0.25)
    km_mid = (km_edges[:-1] + km_edges[1:]) / 2
    n_bins = len(km_mid)
    hist = np.zeros((sample_n, n_bins))
    for start in range(0, sample_n, 256):
        stop = min(start + 256, sample_n)
        idx = sample[start:stop]
        km = haversine_matrix(o_lat[idx], o_lon[idx], d_lat, d_lon)
        which = np.clip(np.digitize(km, km_edges) - 1, 0, n_bins - 1)
        for row in range(stop - start):
            np.add.at(hist[start + row], which[row], d_job)
    print()
    print(f"  calibrating on {sample_n:,} resident-weighted origins, {n_bins} distance bins")

    observed = np.array(target)
    n_bands = len(cwb.TIME_BANDS)

    def evaluate(alpha, beta, floor, terminal, v_max) -> tuple[float, np.ndarray]:
        minutes = travel_minutes(km_mid, terminal=terminal, v_max=v_max)
        band_of_bin = np.full(n_bins, n_bands - 1, dtype=int)
        for band_index, (_id, _label, lo, hi) in enumerate(cwb.TIME_BANDS):
            mask = (minutes > lo) & (minutes <= hi) if lo > 0 else (minutes <= hi)
            band_of_bin[mask] = band_index
        weight = hist * deterrence(minutes, alpha, beta, floor)[None, :]
        row_sum = weight.sum(axis=1, keepdims=True)
        row_sum[row_sum == 0] = 1.0
        flow = weight / row_sum * s_res[:, None]
        shares = np.bincount(band_of_bin, weights=flow.sum(axis=0), minlength=n_bands)
        shares = shares / max(shares.sum(), 1e-9)
        chi = float(((shares - observed) ** 2 / np.maximum(observed, 1e-4)).sum())
        return chi, shares

    best = None
    for terminal in (0.5, 1.0, 1.5, 2.0, 3.0):
        for v_max in (24.0, 28.0, 32.0, 36.0, 42.0, 50.0):
            for floor in np.arange(0.0, 25.1, 2.5):
                for alpha in np.arange(0.0, 4.01, 0.2):
                    for beta in np.arange(0.0, 0.31, 0.02):
                        chi, shares = evaluate(
                            float(alpha), float(beta), float(floor), terminal, v_max
                        )
                        if best is None or chi < best[0]:
                            best = (
                                chi,
                                float(alpha),
                                float(beta),
                                float(floor),
                                float(terminal),
                                float(v_max),
                                list(shares),
                            )
    chi, alpha, beta, floor, terminal, v_max, fitted = best
    print(
        f"  best fit: alpha={alpha:.2f} beta={beta:.3f} floor={floor:.1f} min"
        f"  terminal={terminal:.1f} min  v_max={v_max:.0f} km/h  chi2={chi:.6f}"
    )
    print(f"    {'band':<14}{'model':>8}{'observed':>10}")
    for (_, label, _, _), model, obs in zip(cwb.TIME_BANDS, fitted, target):
        print(f"    {label:<14}{model * 100:7.2f}%{obs * 100:9.2f}%")

    # ---- build the sparse candidate matrix ----
    # ---- candidate destinations, sampled rather than truncated ----
    # Taking the top-K destinations *by gravity weight* looks reasonable and is badly wrong: the
    # top of the weight ordering is dominated by nearby jobs, so the long tail the calibration was
    # fitted to is discarded and the modelled distribution collapses back onto short trips (63.5%
    # in the 6-15 min band against an observed 26.3%). It also leaves most destinations with no
    # candidate origin at all, so the job-side constraint cannot be met.
    #
    # Instead, draw K destinations per origin *with probability proportional to the gravity weight*
    # and give each drawn destination an equal share of the origin's commuters. Then the expected
    # flow to j is residents_i * w_ij / sum_j(w_ij) — exactly the gravity model — while the tail is
    # represented at full weight whenever it is drawn. The Gumbel top-k trick does the weighted
    # sample without replacement in one vectorised pass: perturb log(w) with Gumbel noise and take
    # the largest K.
    print()
    print(f"  sampling {args.top_k} destinations per origin (weighted, without replacement)")
    rows_idx: list[np.ndarray] = []
    cols_idx: list[np.ndarray] = []
    base_w: list[np.ndarray] = []
    minutes: list[np.ndarray] = []
    kms: list[np.ndarray] = []
    k = min(args.top_k, len(dests))
    sampler = np.random.default_rng(20260731)
    for start in range(0, len(origins), args.chunk):
        stop = min(start + args.chunk, len(origins))
        km = haversine_matrix(o_lat[start:stop], o_lon[start:stop], d_lat, d_lon)
        mins = travel_minutes(km, terminal=terminal, v_max=v_max)
        weight = d_job[None, :] * deterrence(mins, alpha, beta, floor)
        keys = np.log(np.maximum(weight, 1e-300)) + sampler.gumbel(size=weight.shape)
        keep = np.argpartition(-keys, k - 1, axis=1)[:, :k]
        rows = np.repeat(np.arange(start, stop)[:, None], k, axis=1)
        rows_idx.append(rows.ravel())
        cols_idx.append(keep.ravel())
        # equal share per drawn destination — this is what makes the sample unbiased
        base_w.append(np.ones(keep.size))
        minutes.append(np.take_along_axis(mins, keep, axis=1).ravel())
        kms.append(np.take_along_axis(km, keep, axis=1).ravel())

    rows = np.concatenate(rows_idx)
    cols = np.concatenate(cols_idx)
    weights = np.concatenate(base_w)
    pair_min = np.concatenate(minutes)
    pair_km = np.concatenate(kms)
    print(f"  candidate pairs: {len(rows):,}")

    # ---- IPF ----
    # Row sums (residents per origin) are a hard requirement: the registry fails a release
    # unless sum(points.residents) == sum(pops.size). Column sums (jobs per destination) are
    # display data, and a sparse top-K matrix often cannot satisfy both — a destination whose
    # only candidate origins are small simply cannot absorb its job count. So IPF runs to
    # improve realism, then the last operation is always a row rescale, and the residual column
    # error is reported rather than hidden.
    flow = weights.copy()
    for iteration in range(args.ipf_iters):
        row_totals = np.bincount(rows, weights=flow, minlength=len(origins))
        scale = np.divide(o_res, row_totals, out=np.zeros_like(o_res), where=row_totals > 0)
        flow *= scale[rows]
        col_totals = np.bincount(cols, weights=flow, minlength=len(dests))
        scale = np.divide(d_job, col_totals, out=np.ones_like(d_job), where=col_totals > 0)
        flow *= scale[cols]
    row_totals = np.bincount(rows, weights=flow, minlength=len(origins))
    scale = np.divide(o_res, row_totals, out=np.zeros_like(o_res), where=row_totals > 0)
    flow *= scale[rows]

    col_totals = np.bincount(cols, weights=flow, minlength=len(dests))
    col_abs = np.abs(col_totals - d_job)
    print(f"  after {args.ipf_iters} IPF sweeps + final row rescale:")
    print(f"    row error   : max {np.abs(np.bincount(rows, weights=flow, minlength=len(origins)) - o_res).max():.6f}")
    print(f"    column error: mean {col_abs.mean():.1f}  median {np.median(col_abs):.1f}  max {col_abs.max():.0f}")
    print(f"                  total |err| {col_abs.sum():,.0f} on {d_job.sum():,.0f} jobs ({col_abs.sum() / d_job.sum():.1%})")

    orphans = int(((np.bincount(rows, minlength=len(origins)) == 0) & (o_res > 0)).sum())
    if orphans:
        print(f"  ! {orphans} origins have no candidate pair")

    # ---- integer pops, per origin so every origin's residents are conserved exactly ----
    order = np.lexsort((-flow, rows))
    by_origin: dict[int, list[int]] = {}
    for index in order:
        by_origin.setdefault(int(rows[index]), []).append(int(index))

    split: list[tuple[int, int, int, float, float]] = []
    for origin, indices in by_origin.items():
        target_size = int(round(o_res[origin]))
        if target_size <= 0:
            continue
        shares = [flow[i] for i in indices]
        # keep only as many destinations as can carry min_flow people, so a 12-resident origin
        # produces one pop rather than fourteen pops of one person
        allowed = max(1, min(len(indices), target_size // max(1, args.min_flow)))
        indices = indices[:allowed]
        shares = shares[:allowed]
        total = sum(shares) or 1.0
        sizes = largest_remainder_float(target_size, [s / total for s in shares])
        for index, size in zip(indices, sizes):
            if size <= 0:
                continue
            mins, km = float(pair_min[index]), float(pair_km[index])
            dest = int(cols[index])
            while size > MAXPOPSIZE:
                split.append((origin, dest, MAXPOPSIZE, mins, km))
                size -= MAXPOPSIZE
            if size > 0:
                split.append((origin, dest, size, mins, km))

    placed = sum(p[2] for p in split)
    print()
    print(f"  pops: {len(split):,}  people placed {placed:,} (residents {o_res.sum():,.0f})")
    if placed != int(o_res.sum()):
        print("  ! people placed does not equal residents — aborting")
        return 1

    sizes = np.array([p[2] for p in split])
    trip_min = np.array([p[3] for p in split])
    trip_km = np.array([p[4] for p in split])
    print(f"  pop size: min {sizes.min()} median {int(np.median(sizes))} max {sizes.max()}")
    print()
    print("  modelled vs observed car-commute time:")
    final = band_shares(trip_min, sizes.astype(float))
    for (_, label, _, _), model, obs in zip(cwb.TIME_BANDS, final, target):
        print(f"    {label:<14}{model * 100:7.2f}%{obs * 100:9.2f}%")
    weighted_km = float((trip_km * sizes).sum() / sizes.sum())
    print()
    print(f"  size-weighted mean straight-line distance: {weighted_km:.2f} km")
    print(f"  size-weighted mean modelled time         : {float((trip_min * sizes).sum() / sizes.sum()):.1f} min")

    payload = {
        "origins": [p["id"] for p in origins],
        "dests": [p["id"] for p in dests],
        "pairs": [[int(o), int(d), int(s), round(m, 2), round(k, 4)] for o, d, s, m, k in split],
        "calibration": {
            "alpha": alpha,
            "beta": beta,
            "floor_min": floor,
            "chi2": chi,
            "target": target,
            "modelled": final,
            "top_k": args.top_k,
            "min_flow": args.min_flow,
            "detour": DETOUR,
            "terminal_min": terminal,
            "speed": [V_MIN, v_max, D_HALF],
        },
    }
    cwb.write_json(cwb.INTERIM / "pairs.json", payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
