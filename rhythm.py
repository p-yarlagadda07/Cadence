import statistics

MIN_SPREAD = 8.0
MIN_TEMPO_SPREAD = 0.04
TEMPO_LIMIT = 2.5

def tempo(sample):
    return sample[-1][1] - sample[0][0]

def features(sample):
    hold = [up - down for down, up in sample]
    down_down = [sample[i + 1][0] - sample[i][0] for i in range(len(sample) - 1)]
    up_down = [sample[i + 1][0] - sample[i][1] for i in range(len(sample) - 1)]
    return hold + down_down + up_down

def build_profile(samples):
    columns = list(zip(*(features(s) for s in samples)))
    mean = [statistics.fmean(col) for col in columns]
    spread = [max(statistics.fmean(abs(x - m) for x in col), MIN_SPREAD) for col, m in zip(columns, mean)]
    times = [tempo(s) for s in samples]
    usual = statistics.fmean(times)
    tempo_spread = max(statistics.fmean(abs(t - usual) for t in times), usual * MIN_TEMPO_SPREAD)
    return {"mean": mean, "spread": spread, "tempo": usual, "tempo_spread": tempo_spread}

def tempo_ok(profile, sample):
    return abs(tempo(sample) - profile["tempo"]) <= TEMPO_LIMIT * profile["tempo_spread"]

def distance(profile, vector):
    return statistics.fmean(abs(x - m) / s for x, m, s in zip(vector, profile["mean"], profile["spread"]))

def leave_one_out(samples):
    scores = []
    for i, sample in enumerate(samples):
        rest = samples[:i] + samples[i + 1:]
        scores.append(distance(build_profile(rest), features(sample)))
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
