"""Club-only forecasts. Artifacts contain portable numeric parameters, never executable pickles."""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import tempfile
from bisect import bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln, logsumexp, softmax
from sklearn import __version__ as sklearn_version
from sklearn.ensemble import GradientBoostingClassifier

UTC = timezone.utc
LONDON = ZoneInfo("Europe/London")
MODEL_DIR = Path(__file__).parent / "assets" / "models"
ALGORITHM = "epl-dc-gbt-1"
OUTCOMES = "HDA"


def _instant(value):
    value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if value.tzinfo is None:
        raise ValueError("A timestamp must include its timezone")
    return value.astimezone(UTC)


def _kickoff(match):
    if match.get("kickoff_utc"):
        return _instant(match["kickoff_utc"])
    return datetime.combine(date.fromisoformat(match["date"]), time.min, LONDON).astimezone(UTC)


def _available(match):
    day_after = date.fromisoformat(match["date"]) + timedelta(days=1)
    end = datetime.combine(day_after, time.min, LONDON).astimezone(UTC)
    if match.get("kickoff_utc"):
        end = max(end, _kickoff(match) + timedelta(hours=3))
    if match.get("result_observed_at"):
        end = max(end, _instant(match["result_observed_at"]))
    return end


def _outcome(home, away):
    return 0 if home > away else 2 if home < away else 1


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def fixture_digest(match):
    return _digest([match.get(k) for k in ("id", "season", "date", "kickoff_utc", "home", "away", "hg", "ag")])


def _schedule_digest(match):
    return _digest([match.get(k) for k in ("id", "season", "date", "kickoff_utc", "home", "away")])


def replay_matches(replay, match):
    if replay and "schedule_digest" in replay:
        return replay["schedule_digest"] == _schedule_digest(match)
    return bool(replay and replay.get("source_fixture_digest") == fixture_digest(match))


@dataclass
class _Club:
    season: int = 0
    games: int = 0
    elo: float = 1500
    recent: deque = field(default_factory=lambda: deque(maxlen=8))
    last: date | None = None

    def indicators(self):
        # Four league-average pseudo-games stabilise early-season estimates.
        n = len(self.recent)
        gf = (5.4 + sum(r[0] for r in self.recent)) / (4 + n)
        ga = (5.4 + sum(r[1] for r in self.recent)) / (4 + n)
        pts = (5.4 + sum(r[2] for r in self.recent)) / (4 + n)
        return gf, ga, pts


class _History:
    def __init__(self):
        self.clubs = {}

    def club(self, name, season):
        club = self.clubs.get(name)
        if club is None or season > club.season + 1:
            return _Club(season=season)
        return club

    def record(self, match):
        year = int(match["season"][:4])
        home, away = (self.club(match[side], year) for side in ("home", "away"))
        h, a = match["hg"], match["ag"]
        expect = 1 / (1 + 10 ** ((away.elo - home.elo - 60) / 400))
        delta = 20 * ((1.0 if h > a else 0.0 if h < a else .5) - expect)
        for club, name, gf, ga, update in ((home, match["home"], h, a, delta),
                                           (away, match["away"], a, h, -delta)):
            club.elo += update
            club.recent.append((gf, ga, 3 if gf > ga else int(gf == ga)))
            club.games += 1
            club.last = date.fromisoformat(match["date"])
            club.season = year
            self.clubs[name] = club

    def features(self, match):
        day = date.fromisoformat(match["date"])
        season = int(match["season"][:4])
        home, away = (self.club(match[side], season) for side in ("home", "away"))
        h, a = home.indicators(), away.indicators()
        rest = [min(30, max(0, (day - c.last).days)) if c.last else 14 for c in (home, away)]
        return [home.elo - away.elo, *h, *a, *rest, min(home.games, 38), min(away.games, 38),
                (day - date(season, 8, 1)).days / 365, home.games, away.games]


def _scored(row):
    return (row is not None and not row.get("source_conflict")
            and row.get("hg") is not None and row.get("ag") is not None)


def _playing_order(row):
    return _kickoff(row), row["id"]


class _Versions:
    """Pinned reconstruction plus immutable local observations. All selection is as-of."""

    def __init__(self, matches, versions=(), archive=None):
        self.history = defaultdict(list)
        for row in versions:
            self.history[row["id"]].append((_instant(row["observed_at"]), row))
        self.base = {m["id"]: m for m in (matches if archive is None else archive)
                     if not m.get("withdrawn") and (archive is not None or m["id"] not in self.history)}
        self.ids = sorted(self.base.keys() | self.history.keys())
        self.times = {}
        events = set()
        for key, entries in self.history.items():
            entries.sort(key=lambda entry: entry[0])
            self.times[key] = [t for t, _ in entries]
            for t, row in entries:
                events.add((t, key))
                if _scored(row):
                    events.add((max(t, _available(row)), key))
        for key, row in self.base.items():
            if _scored(row):
                events.add((_available(row), key))
        def event_order(event):
            at, key = event
            current = self.at(key, at)
            return at, _kickoff(current) if current else at, key
        self.events = sorted(events,key=event_order)

    def at(self, key, cutoff):
        i = bisect_right(self.times.get(key, ()), cutoff)
        row = self.history[key][i-1][1] if i else self.base.get(key)
        return row if row and not row.get("withdrawn") else None

    def fixtures(self, cutoff):
        return [row for key in self.ids if (row := self.at(key, cutoff)) is not None]

    def score(self, key, cutoff):
        row = self.at(key, cutoff)
        return row if _scored(row) and _available(row) <= cutoff else None

    def finished(self, cutoff):
        pairs = [(_available(m), m) for key in self.ids if (m := self.score(key, cutoff)) is not None]
        return sorted(pairs, key=lambda pair: (pair[0], *_playing_order(pair[1])))

    def features(self, targets, *, schedule_keys=False):
        # Append new scores in playing order. Rebuild after a late observation, correction or withdrawal.
        state = _History()
        known = {}
        position = 0
        last = None
        result = {}
        for cutoff, target in sorted(targets, key=lambda pair: (pair[0], pair[1]["id"])):
            rebuild = False
            while position < len(self.events) and self.events[position][0] <= cutoff:
                at, key = self.events[position]
                position += 1
                new = self.score(key, at)
                previous = known.get(key)
                if previous and new and fixture_digest(previous) == fixture_digest(new):
                    continue
                if previous is not None:
                    del known[key]
                    rebuild = True
                if new is not None:
                    known[key] = new
                    order = _playing_order(new)
                    if last is not None and order < last:
                        rebuild = True
                    if not rebuild:
                        state.record(new)
                    last = max(order, last) if last else order
            if rebuild:
                state = _History()
                for row in sorted(known.values(), key=_playing_order):
                    state.record(row)
                last = max(map(_playing_order, known.values()), default=None)
            current = self.at(target["id"], cutoff)
            if current is not None and _schedule_digest(current) == _schedule_digest(target):
                key = _schedule_digest(target) if schedule_keys else target["id"]
                result[key] = state.features(current)
        return result


_LATEST = datetime.max.replace(tzinfo=UTC)


def _evaluation_view(source, as_of):
    end = _instant(as_of) if as_of is not None else _LATEST
    finished = source.finished(end)
    features = source.features([(_kickoff(m)-timedelta(days=1), m) for _, m in finished])
    return finished, features


def _timeline(matches):
    source = _Versions(matches)
    features = source.features([(_kickoff(m)-timedelta(days=1), m) for m in matches])
    return source.finished(_LATEST), features


def _dc_fit(matches, cutoff):
    # Penalised Poisson likelihood with low-score Dixon–Coles tau corrections.
    # Positive and negative strengths are shrunk to the league prior.
    if not matches:
        return {"intercept": math.log(1.20), "home": math.log(1.48 / 1.20), "rho": -.06, "clubs": {}}
    recent = [m for m in matches if (cutoff.date() - date.fromisoformat(m["date"])).days < 5 * 365]
    if not recent:
        return _dc_fit([], cutoff)
    clubs = sorted({m[k] for m in recent for k in ("home", "away")})
    ix = {k: i for i, k in enumerate(clubs)}
    n = len(clubs)
    hi, ai = (np.array([ix[m[s]] for m in recent]) for s in ("home", "away"))
    gh, ga = (np.array([m[s] for m in recent]) for s in ("hg", "ag"))
    days = np.array([(cutoff.date() - date.fromisoformat(m["date"])).days for m in recent])
    w = 2.0 ** (-days / 365.)
    z00, z01, z10, z11 = ((gh == h) & (ga == a) for h, a in ((0, 0), (0, 1), (1, 0), (1, 1)))

    def loss(x):
        atk, weak = x[:n], x[n:2*n]
        lh = x[-3] + x[-2] + atk[hi] + weak[ai]
        la = x[-3] + atk[ai] + weak[hi]
        h, a = np.exp(lh), np.exp(la)
        coeff = -z00.astype(float) * h*a + z01*h + z10*a - z11
        rho = x[-1]
        tau = 1 + rho * coeff
        if np.min(tau) <= 0:
            return 1e15, np.zeros_like(x)
        adj = w * rho / tau
        dh = w * (h-gh) - adj * (-z00.astype(float) * h*a + z01*h)
        da = w * (a-ga) - adj * (-z00.astype(float) * h*a + z10*a)
        val = np.sum(w * (h+a-gh*lh-ga*la-np.log(tau))) + 3 * (atk@atk + weak@weak) + 15*rho*rho
        grad = np.r_[np.bincount(hi, dh, n) + np.bincount(ai, da, n) + 6*atk,
                     np.bincount(ai, dh, n) + np.bincount(hi, da, n) + 6*weak,
                     dh.sum()+da.sum(), dh.sum(), -np.sum(w*coeff/tau) + 30*rho]
        return val, grad

    initial = np.r_[np.zeros(2*n), math.log(1.2), .18, -.05]
    fit = minimize(loss, initial, jac=True, method="L-BFGS-B",
                   bounds=[(-1.2, 1.2)] * (2*n) + [(-1, 1), (-.5, .8), (-.18, .08)],
                   options={"maxiter": 160, "ftol": 1e-9})
    if not np.isfinite(fit.fun) or fit.fun >= 1e15 or not fit.success:
        raise RuntimeError(f"Dixon-Coles fit failed: {fit.message}")
    x = fit.x
    return {"intercept": x[-3], "home": x[-2], "rho": x[-1],
            "clubs": {k: [x[i], x[n+i]] for i, k in enumerate(clubs)}}


def _booster(matches, features):
    if len(matches) < 180:
        return None
    X = np.array([features[m["id"]][:12] for m in matches])
    y = np.array([_outcome(m["hg"], m["ag"]) for m in matches])
    if len(np.unique(y)) < 3:
        return None
    clf = GradientBoostingClassifier(n_estimators=40, max_depth=2, learning_rate=.045,
                                     min_samples_leaf=22, random_state=373)
    clf.fit(X, y)
    stages = []
    for group in clf.estimators_:
        stage = []
        for est in group:
            tree = est.tree_
            stage.append(list(zip(tree.feature.tolist(), tree.threshold.tolist(), tree.children_left.tolist(),
                                  tree.children_right.tolist(), tree.value[:, 0, 0].tolist())))
        stages.append(stage)
    return {"prior": clf.init_.class_prior_.tolist(), "stages": stages, "rate": clf.learning_rate}


def _boost_predict(booster, x):
    sums = np.log(booster["prior"])
    for group in booster["stages"]:
        for i, tree in enumerate(group):
            index = 0
            while tree[index][2] >= 0:
                feature, threshold, left, right, _ = tree[index]
                index = left if x[feature] <= threshold else right
            sums[i] += booster["rate"] * tree[index][4]
    return softmax(sums)


_GOALS = np.arange(21)
_LFACT = gammaln(_GOALS + 1)


def _matrix(dc, match, x):
    h = dc["clubs"].get(match["home"], (0, 0)) if x[9] else (0, 0)
    a = dc["clubs"].get(match["away"], (0, 0)) if x[10] else (0, 0)
    lambdas = np.exp([dc["intercept"] + dc["home"] + h[0] + a[1], dc["intercept"] + a[0] + h[1]])
    lh, la = lambdas
    p, q = (np.exp(_GOALS * math.log(v) - v - _LFACT) for v in lambdas)
    grid = np.outer(p, q)
    rho = float(np.clip(dc["rho"], -.99/max(lh, la), min(.99, .99/(lh*la))))
    grid[:2, :2] *= [[1-lh*la*rho, 1+lh*rho], [1+la*rho, 1-rho]]
    grid /= grid.sum()
    return np.array([np.tril(grid, -1).sum(), np.trace(grid), np.triu(grid, 1).sum()]), grid, lambdas


def _temperature(p, temp):
    return softmax(np.log(np.clip(p, 1e-10, 1)) / temp, axis=-1)


def _select_params(previous, season_validation, features):
    if previous is None or previous["booster"] is None or len(season_validation) < 120:
        return .5, 1.
    dcs, trees, outcomes = [], [], []
    for m in season_validation:
        x = features[m["id"]]
        dcs.append(_matrix(previous["dc"], m, x)[0])
        trees.append(_boost_predict(previous["booster"], x))
        outcomes.append(_outcome(m["hg"], m["ag"]))
    dc, tree = np.array(dcs), np.array(trees)
    candidates = []
    for weight in (0., .25, .5, .75, 1.):
        for temp in (.85, .925, 1., 1.075, 1.15):
            probs = _temperature((1-weight)*dc + weight*tree, temp)
            candidates.append((-np.log(probs[np.arange(len(dc)), outcomes]).mean(), abs(temp-1), weight, temp))
    return min(candidates)[2:]


def _predict(match, x, artifact, cutoff, *, historical):
    dc, grid, _ = _matrix(artifact["dc"], match, x)
    boosted = _boost_predict(artifact["booster"], x) if artifact["booster"] else dc
    weight = artifact["boost_weight"]
    probs = _temperature((1-weight)*dc + weight*boosted, artifact["temperature"])
    # Rescale each scoreline within its outcome bucket to the calibrated 1X2 mass.
    h, a = np.indices(grid.shape)
    recalibrated = grid * np.choose(np.where(h > a, 0, np.where(h < a, 2, 1)), probs/dc)
    largest = np.argsort(-recalibrated, axis=None, kind="stable")[:3]
    win = 1 / (1 + 10 ** (-(x[0]+60)/400))
    draw = .27 * (1 - abs(2*win-1))
    return {
        "probs": probs.tolist(), "elo_probs": [(1-draw)*win, draw, (1-draw)*(1-win)],
        "dc_probs": dc.tolist(), "tree_probs": boosted.tolist() if artifact["booster"] else None,
        "xg": [float((recalibrated*h).sum()), float((recalibrated*a).sum())],
        "top_scores": [{"home": int(idx//grid.shape[1]), "away": int(idx%grid.shape[1]),
                        "prob": float(recalibrated.flat[idx])} for idx in largest],
        "samples": {"home": int(x[12]), "away": int(x[13])},
        "form": {"home": {"gf": x[1], "ga": x[2], "points": x[3]},
                 "away": {"gf": x[4], "ga": x[5], "points": x[6]}},
        "form_window": {"max_games": 8, "prior_games": 4, "home_games": min(8, int(x[12])),
                        "away_games": min(8, int(x[13]))},
        "model_version": artifact["version"], "cutoff": cutoff.isoformat(),
        "trained_through": artifact["trained_through"], "historical_reconstruction": historical,
    }


def _write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if path.suffix == ".gz":
        data = gzip.compress(data, compresslevel=6, mtime=0)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = stream.name
        stream.write(data)
    try:
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _read(path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == ".gz" else data)


def _fit_seasons(source, output, as_of):
    end_at = _instant(as_of) if as_of is not None else _LATEST
    finished = source.finished(end_at)
    if not finished:
        raise ValueError("No complete historical matches")
    # Each combination of fixture and schedule may have a different prediction cutoff.
    schedules = {_schedule_digest(m): m for m in source.base.values()}
    for entries in source.history.values():
        for observed, row in entries:
            if observed <= end_at and not row.get("withdrawn"):
                schedules[_schedule_digest(row)] = row
    known_features = source.features([(_kickoff(m)-timedelta(days=1), m) for m in schedules.values()],
                                     schedule_keys=True)
    grouped = defaultdict(list)
    for m in source.fixtures(end_at):
        if m.get("date"):
            grouped[int(m["season"][:4])].append(m)
    start, end = min(grouped), max(grouped)
    previous = None
    index = []
    final_season_complete = sum(1 for _, m in finished if int(m["season"][:4]) == end) >= 380
    for year in range(start, end + 1 + int(final_season_complete)):
        # July 1 is the regular preseason boundary. Never train on results from
        # the target season, including if its earliest fixture is unexpectedly early.
        cutoff = datetime(year, 7, 1, tzinfo=UTC)
        known_schedule = [m for m in source.fixtures(min(cutoff,end_at)) if int(m["season"][:4]) == year]
        if known_schedule:
            cutoff = min(cutoff, min(_kickoff(m) for m in known_schedule) - timedelta(days=1))
        if cutoff > end_at:
            break
        history = [m for _, m in source.finished(cutoff) if int(m["season"][:4]) < year]
        features = {m["id"]: x for m in history if (x := known_features.get(_schedule_digest(m))) is not None}
        validation = [m for m in history if int(m["season"][:4]) == year-1 and m["id"] in features]
        train_rows = history[-7*380:]
        weight, temp = _select_params(previous, validation, features)
        source_hash = _digest([[m["id"],m["date"],m.get("kickoff_utc"),m["home"],m["away"],m["hg"],m["ag"],
                                m.get("result_observed_at")] for m in history])
        artifact = {"algorithm": ALGORITHM, "dc": _dc_fit(train_rows, cutoff),
                    "booster": _booster([m for m in train_rows if m["id"] in features], features),
                    "valid_from": cutoff.isoformat(), "training_digest":source_hash,
                    "trained_through": max((_available(m) for m in history),default=None),
                    "boost_weight": weight, "temperature": temp, "n_training":len(train_rows)}
        if artifact["trained_through"] is not None:
            artifact["trained_through"] = artifact["trained_through"].isoformat()
        version = f"{ALGORITHM}-{year}-{_digest(artifact)[:12]}"
        artifact["version"] = version
        _write(output / (version + ".json.gz"), artifact)
        index.append({k: artifact[k] for k in ("version","valid_from","trained_through","training_digest","n_training")})
        previous = artifact
    manifest = {"algorithm": ALGORITHM, "sklearn":sklearn_version, "seasons": index}
    _write(output / "manifest.json", manifest)
    return manifest


def train(matches, output_dir=None, *, versions=(), archive=None, as_of=None):
    """Rebuild per-season model artifacts. Callers can choose a writable output directory."""
    return _fit_seasons(_Versions(matches,versions,archive), Path(output_dir or MODEL_DIR), as_of)


def _select_artifact(manifest, cutoff, version=None):
    valid = [s for s in manifest["seasons"] if _instant(s["valid_from"]) <= cutoff
             and (s["trained_through"] is None or _instant(s["trained_through"]) <= cutoff)
             and (not version or s["version"] == version)]
    if not valid:
        raise ValueError("No frozen model eligible at this cutoff")
    return max(valid, key=lambda s: s["valid_from"])["version"]


def forecast_many(targets, matches, cutoff=None, model_dir=None, version=None, *, versions=(), archive=None):
    """Forecast matching schedules in one observation sweep. None means each fixture's T−24h."""
    targets = list(targets)
    if not targets:
        return {}
    common_cutoff = _instant(cutoff) if cutoff is not None else None
    requested = []
    for match in targets:
        boundary = _kickoff(match) - timedelta(days=1)
        when = common_cutoff or boundary
        if when > boundary:
            raise ValueError("Forecast inputs must be frozen by T-24h")
        requested.append((when, match))
    source = _Versions(matches, versions, archive)
    features = source.features(requested)
    path = Path(model_dir or MODEL_DIR)
    manifest = _read(path/"manifest.json")
    now = datetime.now(UTC)
    artifacts = {}
    out = {}
    for when, match in requested:
        if match["id"] not in features:
            continue
        key = _select_artifact(manifest, when, version)
        if key not in artifacts:
            artifacts[key] = _read(path/(key+".json.gz"))
        out[match["id"]] = _predict(match, features[match["id"]], artifacts[key], when,
                                    historical=not match.get("kickoff_utc") or _kickoff(match)-timedelta(days=1)<now)
    return out


def forecast(match, matches, cutoff=None, model_dir=None, version=None, *, versions=(), archive=None):
    forecasts = forecast_many([match],matches,cutoff,model_dir,version,versions=versions,archive=archive)
    if match["id"] not in forecasts:
        raise ValueError("Fixture schedule was not known at forecast cutoff")
    return forecasts[match["id"]]


def _summarize(rows):
    groups = defaultdict(list)
    bins = [[] for _ in range(10)]
    worst = []
    for m,p in rows:
        y = _outcome(m["hg"],m["ag"])
        a, elo = p["probs"], p["elo_probs"]
        groups[m["season"]].append([-math.log(a[y]),sum((v-float(i==y))**2 for i,v in enumerate(a)),
                                    float(int(np.argmax(a))==y),-math.log(elo[y])])
        for i, v in enumerate(a):
            bins[min(int(v * 10),9)].append((v,int(i==y)))
        worst.append({"id":m["id"],"home":m["home"],"away":m["away"],"season":m["season"],
                      "prob":a[y],"actual":OUTCOMES[y]})
    seasons = []
    for season, values in sorted(groups.items()):
        seasons.append(dict(season=season,n=len(values),**dict(zip(("log_loss","brier","accuracy","elo_loss"),
                                                                    np.mean(values,axis=0).tolist()))))
    calibration = [{"n":len(b),"p":sum(r[0] for r in b)/len(b),"observed":sum(r[1] for r in b)/len(b)}
                   for b in bins if b]
    return {"seasons":seasons, "calibration":calibration, "worst":sorted(worst,key=lambda r:r["prob"])[:30],
            "n":len(rows),"warmup_seasons":2,"scope":"independent_historical_reconstruction"}


_COMPONENTS = {"elo":"elo_probs", "dc":"dc_probs", "tree":"tree_probs", "a":"probs"}


def _class_bins(values, truth):
    bucket = np.minimum((values*10).astype(int), 9)
    bins = []
    for b in range(10):
        mask = bucket == b
        bins.append({"lo": b/10, "hi": (b+1)/10, "n": int(mask.sum()),
                     "p": float(values[mask].mean()) if mask.any() else None,
                     "observed": float(truth[mask].mean()) if mask.any() else None})
    return bins


def _component_scores(rows):
    common = [row for row in rows if all(row["probs"][k] is not None for k in _COMPONENTS)]
    y = np.array([OUTCOMES.index(row["actual"]) for row in common], dtype=int)
    truth = np.eye(3)[y]
    scores, calibration = {}, {}
    for k in _COMPONENTS:
        missing = sum(row["probs"][k] is None for row in rows)
        probs = np.array([row["probs"][k] for row in common]).reshape(-1,3)
        scores[k] = {"n":len(common), "missing":missing,
                     "log_loss":float(-np.log(probs[np.arange(len(y)),y].clip(1e-15)).mean()) if common else None,
                     "brier":float(((probs-truth)**2).sum(axis=1).mean()) if common else None,
                     "accuracy":float((probs.argmax(axis=1)==y).mean()) if common else None}
        calibration[k] = {side:_class_bins(probs[:,i],truth[:,i]) for i, side in enumerate(OUTCOMES)}
    return {"n":len(common), "total":len(rows), "scores":scores, "calibration":calibration}


def _diagnostics(rows):
    by_season = defaultdict(list)
    records = []
    for m, p in rows:
        record = {"id":m["id"],"season":m["season"],"date":m["date"],"home":m["home"],"away":m["away"],
                  "actual":OUTCOMES[_outcome(m["hg"],m["ag"])],"cutoff":p["cutoff"],"model_version":p["model_version"],
                  "probs":{k:p[field] for k,field in _COMPONENTS.items()}}
        records.append(record)
        by_season[m["season"]].append(record)
    all_scores = _component_scores(records)
    return {"models":list(_COMPONENTS), "n":all_scores["n"], "total":len(records), "overall":all_scores["scores"],
            "calibration":all_scores["calibration"],
            "seasons":[{"season":season,**_component_scores(group)} for season,group in sorted(by_season.items())]}, records


def backtest(matches, output_dir=None, *, versions=(), archive=None, as_of=None):
    source = _Versions(matches, versions, archive)
    path = Path(output_dir or MODEL_DIR)
    manifest = _read(path/"manifest.json") if (path/"manifest.json").is_file() else _fit_seasons(source,path,as_of)
    finished, features = _evaluation_view(source, as_of)
    replays, rows, artifacts = {}, [], {}
    start = min((int(m["season"][:4]) for _,m in finished), default=None)
    missed_schedules = 0
    for _, m in finished:
        if int(m["season"][:4]) < start+2:
            continue
        if m["id"] not in features:
            missed_schedules += 1
            continue
        cutoff = _kickoff(m)-timedelta(days=1)
        v = _select_artifact(manifest,cutoff)
        if v not in artifacts:
            artifacts[v] = _read(path/(v+".json.gz"))
        p = _predict(m,features[m["id"]],artifacts[v],cutoff,historical=True)
        p["schedule_digest"] = _schedule_digest(m)
        replays[m["id"]] = p
        rows.append((m,p))
    report = _summarize(rows)
    report["diagnostics"], report["rows"] = _diagnostics(rows)
    report["schedule_unknown_at_cutoff"] = missed_schedules
    report["labels_as_of"] = _instant(as_of).isoformat() if as_of is not None else None
    report["artifact_digest"] = _digest(manifest)
    _write(path/"replays.json.gz",replays)
    _write(path/"report.json",report)
    return report


def load_replays(model_dir=None):
    return _read(Path(model_dir or MODEL_DIR)/"replays.json.gz")


def load_report(model_dir=None):
    return _read(Path(model_dir or MODEL_DIR)/"report.json")


def _vector(values):
    p = np.asarray(values,dtype=float)
    if p.shape != (3,) or not np.all(np.isfinite(p)) or np.any(p <= 0) or not np.isclose(p.sum(), 1, atol=1e-6):
        raise ValueError("Expected three positive, normalized probabilities")
    return p


def blend(a, market):
    return (.2 * _vector(a) + .8 * _vector(market)).tolist()


def fit_challenger(rows):
    """Fit only the first 380 verified, resolved fixtures; never modify the baseline."""
    eligible = sorted((r for r in rows if r.get("timing_verified") is True and r.get("actual") in list(OUTCOMES)),
                      key=lambda r: (_instant(r["cutoff"]), r["id"]))
    ids = [r["id"] for r in eligible]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate development fixtures")
    if len(eligible) < 380:
        raise ValueError("380 verified development fixtures required")
    sample = eligible[:380]
    x = np.array([np.log(_vector(r["probs"])/_vector(r["market"])) for r in sample])
    y = np.array([OUTCOMES.index(r["actual"]) for r in sample])
    offset = np.log([_vector(r["market"]) for r in sample])
    def objective(w):
        weights = w.reshape(3,3)
        logits = offset + x@weights
        p = softmax(logits, axis=1)
        val = np.sum(logsumexp(logits,axis=1)-logits[np.arange(len(y)),y]) + 5*np.sum(weights**2)
        p[np.arange(len(y)),y] -= 1
        return val,(x.T@p+10*weights).ravel()
    fit = minimize(objective,np.zeros(9),jac=True,method="L-BFGS-B")
    if not fit.success:
        raise RuntimeError(fit.message)
    return {"version":"residual-"+_digest(sample)[:16],"weights":fit.x.reshape(3,3).tolist(),
            "development_ids":ids[:380],"last_cutoff":sample[-1]["cutoff"]}


def challenger_forecast(artifact, a, market):
    m = _vector(market)
    return softmax(np.log(m)+np.log(_vector(a)/m)@np.array(artifact["weights"])).tolist()
