import statistics
import sys
from pathlib import Path

import cmu_benchmark as cb
import rhythm


def setup(people):
    out = {}
    for subject, rows in people.items():
        train, genuine, _ = cb.split(people, subject, cb.familiar_8)
        profile = rhythm.build_profile([cb.to_sample(r) for r in train])

        def score(row, profile=profile):
            sample = cb.to_sample(row)
            if not rhythm.tempo_ok(profile, sample):
                return float("inf")
            return rhythm.distance(profile, sample)

        mine = [score(r) for r in genuine]
        pairs = [
            (score(theirs[a]), score(theirs[b]))
            for other, theirs in people.items() if other != subject
            for a, b in ((0, 1), (2, 3))
        ]
        singles = [s for pair in pairs for s in pair]
        out[subject] = (mine, pairs, singles)
    return out


def threshold_at_eer(mine, singles):
    points = sorted({v for v in mine + singles if v != float("inf")})
    return min(points, key=lambda t: abs(
        sum(v > t for v in mine) / len(mine) - sum(v <= t for v in singles) / len(singles)))


def decide(first, second, t, low, high):
    if low >= high:
        return first <= t, False
    if first <= t * low:
        return True, False
    if first > t * high:
        return False, True
    return (first + second) / 2 <= t, True


def evaluate(data, subjects, low, high):
    blocked, let_in, asked = [], [], []
    for s in subjects:
        mine, pairs, singles = data[s]
        t = threshold_at_eer(mine, singles)
        own = [decide(a, b, t, low, high) for a, b in zip(mine[::2], mine[1::2])]
        other = [decide(a, b, t, low, high) for a, b in pairs]
        blocked.append(sum(not ok for ok, _ in own) / len(own))
        let_in.append(sum(ok for ok, _ in other) / len(other))
        asked.append(sum(q for _, q in own) / len(own))
    return statistics.fmean(blocked), statistics.fmean(let_in), statistics.fmean(asked)


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else cb.DATA
    people = cb.load(path)
    data = setup(people)
    subjects = sorted(people)
    halves = subjects[::2], subjects[1::2]
    rules = [
        ("One typing", 1, 1),
        ("Retype if score is 80% to 130% of threshold", 0.8, 1.3),
        ("Retype if score is 70% to 150% of threshold", 0.7, 1.5),
        ("Retype only when it would have failed", 1.0, 1.3),
        ("Always type twice", 0, 99),
    ]
    print(f"{'rule':46} {'you blocked':>12} {'impostor in':>12} {'you asked':>10}   halves")
    for name, low, high in rules:
        b, i, q = evaluate(data, subjects, low, high)
        split_halves = "  ".join("{:.1%} / {:.1%}".format(*evaluate(data, h, low, high)[:2]) for h in halves)
        print(f"{name:46} {b:12.1%} {i:12.1%} {q:10.0%}   {split_halves}")


if __name__ == "__main__":
    main()
