import statistics

MIN_SPREAD = 8.0
MIN_TEMPO_SPREAD = 0.07
TEMPO_LIMIT = 3
RECENT_TEMPO = 8
SETTLED_PROFILE = 12
SHIFT_PENALTY = 4.0

def tempo(sample):
    return sample[-1][1] - sample[0][0]

def features(sample):
    hold = [k[1] - k[0] for k in sample]
    down_down = [sample[i + 1][0] - sample[i][0] for i in range(len(sample) - 1)]
    return hold + down_down

def shifted(sample):
    return tuple(i for i, k in enumerate(sample) if len(k) == 5)

def spread_of(values, m):
    return max(statistics.fmean(abs(x - m) for x in values), MIN_SPREAD)

def shift_profile(samples):
    positions = {shifted(s) for s in samples}
    if len(positions) != 1:
        return []
    out = []
    for i in positions.pop():
        leads = [s[i][0] - s[i][2] for s in samples]
        lags = [s[i][3] - s[i][1] for s in samples]
        sides = [s[i][4] for s in samples]
        lead, lag = statistics.fmean(leads), statistics.fmean(lags)
        out.append({
            "pos": i,
            "lead": lead,
            "lead_spread": spread_of(leads, lead),
            "lag": lag,
            "lag_spread": spread_of(lags, lag),
            "side": max(set(sides), key=sides.count),
        })
    return out

def build_profile(samples):
    columns = list(zip(*(features(s) for s in samples)))
    mean = [statistics.fmean(col) for col in columns]
    spread = [spread_of(col, m) for col, m in zip(columns, mean)]
    times = [tempo(s) for s in samples[-RECENT_TEMPO:]]
    usual = statistics.fmean(times)
    tempo_spread = max(statistics.fmean(abs(t - usual) for t in times), usual * MIN_TEMPO_SPREAD)
    return {
        "mean": mean,
        "spread": spread,
        "tempo": usual,
        "tempo_spread": tempo_spread,
        "settled": len(samples) >= SETTLED_PROFILE,
        "shift": shift_profile(samples),
    }

def tempo_ok(profile, sample):
    limit = TEMPO_LIMIT if profile["settled"] else TEMPO_LIMIT + 1
    return abs(tempo(sample) - profile["tempo"]) <= limit * profile["tempo_spread"]

def distance(profile, sample):
    parts = [abs(x - m) / s for x, m, s in zip(features(sample), profile["mean"], profile["spread"])]
    for key in profile["shift"]:
        k = sample[key["pos"]]
        if len(k) < 5:
            parts += [SHIFT_PENALTY, SHIFT_PENALTY]
            continue
        parts.append(abs((k[0] - k[2]) - key["lead"]) / key["lead_spread"])
        parts.append(abs((k[3] - k[1]) - key["lag"]) / key["lag_spread"])
        if k[4] != key["side"]:
            parts.append(SHIFT_PENALTY)
    return statistics.fmean(parts)

def leave_one_out(samples):
    scores = []
    for i, sample in enumerate(samples):
        rest = samples[:i] + samples[i + 1:]
        scores.append(distance(build_profile(rest), sample))
    return scores

def drop_outliers(samples, keep_at_least=6):
    samples = list(samples)
    while len(samples) > keep_at_least:
        scores = leave_one_out(samples)
        worst = max(range(len(scores)), key=scores.__getitem__)
        others = scores[:worst] + scores[worst + 1:]
        if scores[worst] < 2 * statistics.median(others):
            break
        samples.pop(worst)
    return samples

def auto_threshold(samples):
    scores = leave_one_out(samples)
    t = statistics.fmean(scores) + 2 * statistics.pstdev(scores)
    return min(max(t, 1.5), 4.0)
