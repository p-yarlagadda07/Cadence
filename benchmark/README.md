# CMU benchmark

This runs Cadence's matching code (`rhythm.py`) on the
[CMU Keystroke Dynamics Benchmark](https://www.cs.cmu.edu/~keystroke/), the
dataset from Killourhy and Maxion (2009). 51 people each typed the password
`.tie5Roanl` 400 times over 8 sessions.

## Getting the data

Download `DSL-StrongPasswordData.csv` from the
[benchmark page](https://www.cs.cmu.edu/~keystroke/) (there's also a
[mirror on the author's site](https://roymaxion.github.io/projects/keystroke-benchmark/benchmark-data-set.html))
and put it in `benchmark/data/`. The script checks its MD5 against the
published one, `470235f96568f28f9ea0da62234ec857`.

```
python benchmark/cmu_benchmark.py
```

It takes a few seconds and needs nothing beyond the Python standard library.

## How it's tested

Same procedure as the paper's evaluation script. For each of the 51 typists:

- the detector is trained on some of their own typing
- genuine attempts are their last 200 typings (sessions 5 to 8)
- impostor attempts are the first 5 typings of each of the other 50 people

The equal error rate is worked out for each typist, then averaged.

Each row of the CSV is turned into the same `(press, release)` timings the
browser sends, so the benchmark runs the real `build_profile`, `distance` and
`tempo_ok` functions, not a copy of them.

## Results

| Detector | Trained on | EER | Std dev |
| --- | --- | --- | --- |
| Paper's scaled Manhattan (my reproduction) | first 200 typings | 9.6% | 6.9% |
| Cadence rhythm score | first 200 typings | 9.2% | 7.1% |
| Cadence rhythm + speed check | first 200 typings | 9.4% | 7.4% |
| Paper's scaled Manhattan | 8 familiar typings | 8.4% | 4.5% |
| Cadence rhythm + speed check | 8 familiar typings | **7.9%** | 4.4% |
| Cadence rhythm + speed check | first 15 typings | 30.0% | 20.5% |

The first row matches the paper's published 9.62% ± 6.94%, which is how I
know the evaluation itself is right.

"8 familiar typings" means the last 8 typings of session 4, after the person
had already typed the password 150 times. That's the closest match to signing
up for Cadence, since people sign up with a password they already know.

What I took from this:

- **Cadence's rhythm score beats the paper's version** in every setting. The
  main difference is that Cadence leaves out the release-to-press times, which
  only repeat information already in the other two measurements.
- **8 familiar typings are enough.** Training on them gives 7.9%, better than
  training on 200 typings that include someone still learning the password.
  It also lines up with the roughly 7% I measured with real people.
- **Signing up with a password you don't know yet is a bad idea.** Trained on
  someone's first 15 attempts, the error rate jumps to 30%, because their
  rhythm keeps changing as they learn it.
- **The speed check doesn't help here.** It costs a few tenths of a percent.
  The impostors in this dataset are just other people typing normally, and the
  rhythm score already catches them. The speed check is there for people
  copying your rhythm at a different pace, and this dataset has none of those.

The Shift timing in Cadence isn't tested here, because the dataset records
Shift and R as a single key.
