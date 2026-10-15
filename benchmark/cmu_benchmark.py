import csv
import hashlib
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rhythm

DATA = Path(__file__).parent / "data" / "DSL-StrongPasswordData.csv"
EXPECTED_MD5 = "470235f96568f28f9ea0da62234ec857"
KEYS = ["period", "t", "i", "e", "five", "Shift.r", "o", "a", "n", "l", "Return"]


def load(path):
    digest = hashlib.md5(path.read_bytes()).hexdigest()
    if digest != EXPECTED_MD5:
        print(f"warning: md5 is {digest}, expected {EXPECTED_MD5}")
    people = defaultdict(list)
    with path.open() as f:
        for row in csv.DictReader(f):
            people[row["subject"]].append(row)
    return people


def to_sample(row):
    down = 0.0
    out = []
    for i, key in enumerate(KEYS):
        hold = float(row[f"H.{key}"]) * 1000
        out.append((down, down + hold))
        if i < len(KEYS) - 1:
            down += float(row[f"DD.{key}.{KEYS[i + 1]}"]) * 1000
    return out


def raw_features(row):
    return [float(v) for k, v in row.items() if k[:2] in ("H.", "DD", "UD")]


def first_200(rows):
    return [r for r in rows if int(r["sessionIndex"]) <= 4]


def familiar_8(rows):
    return [r for r in rows if int(r["sessionIndex"]) == 4][-8:]


def learning_15(rows):
    return [r for r in rows if int(r["sessionIndex"]) == 1][:15]


def split(people, subject, pick):
    rows = people[subject]
    train = pick(rows)
    genuine = [r for r in rows if int(r["sessionIndex"]) > 4]
    impostors = [
        r for other, theirs in people.items() if other != subject
        for r in theirs if int(r["sessionIndex"]) == 1 and int(r["rep"]) <= 5
    ]
    return train, genuine, impostors


def equal_error_rate(genuine, impostor):
    points = sorted(set(genuine + impostor))
    best = None
    for t in [points[0] - 1] + points:
        frr = sum(g > t for g in genuine) / len(genuine)
        far = sum(i <= t for i in impostor) / len(impostor)
        if best is None or abs(frr - far) < abs(best[0] - best[1]):
            best = (frr, far)
    return (best[0] + best[1]) / 2


def paper_detector(train, genuine, impostors):
    vectors = [raw_features(r) for r in train]
    cols = list(zip(*vectors))
    mean = [statistics.fmean(c) for c in cols]
    mad = [statistics.fmean(abs(x - m) for x in c) for c, m in zip(cols, mean)]

    def score(r):
        return sum(abs(x - m) / s for x, m, s in zip(raw_features(r), mean, mad))

    return [score(r) for r in genuine], [score(r) for r in impostors]


def cadence_detector(use_speed):
    def run(train, genuine, impostors):
        profile = rhythm.build_profile([to_sample(r) for r in train])

        def score(r):
            sample = to_sample(r)
            if use_speed and not rhythm.tempo_ok(profile, sample):
                return float("inf")
            return rhythm.distance(profile, sample)

        return [score(r) for r in genuine], [score(r) for r in impostors]
    return run


def evaluate(people, detector, pick):
    rates = []
    for subject in sorted(people):
        train, genuine, impostors = split(people, subject, pick)
        g, i = detector(train, genuine, impostors)
        rates.append(equal_error_rate(g, i))
    return statistics.fmean(rates), statistics.stdev(rates)


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA
    if not path.exists():
        sys.exit(f"Dataset not found at {path}. See benchmark/README.md for where to get it.")
    people = load(path)
    print(f"{len(people)} typists, {sum(len(v) for v in people.values())} password entries\n")
    runs = [
        ("Paper's scaled Manhattan (reproduction)", paper_detector, first_200, "first 200"),
        ("Cadence rhythm score", cadence_detector(False), first_200, "first 200"),
        ("Cadence rhythm + speed check", cadence_detector(True), first_200, "first 200"),
        ("Paper's scaled Manhattan", paper_detector, familiar_8, "8 familiar"),
        ("Cadence rhythm + speed check", cadence_detector(True), familiar_8, "8 familiar"),
        ("Cadence rhythm + speed check", cadence_detector(True), learning_15, "15 learning"),
    ]
    print(f"{'Detector':42} {'Training':>12} {'EER':>7} {'Std dev':>8}")
    for name, detector, pick, label in runs:
        mean, sd = evaluate(people, detector, pick)
        print(f"{name:42} {label:>12} {mean:>7.1%} {sd:>8.1%}")


if __name__ == "__main__":
    main()
