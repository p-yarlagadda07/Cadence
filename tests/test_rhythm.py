import rhythm


def sample(holds, gaps, start=0.0):
    t = start
    keys = []
    for i, h in enumerate(holds):
        keys.append((t, t + h))
        if i < len(gaps):
            t += gaps[i]
    return keys


def shifted(keys, pos, lead, lag, side=0):
    keys = list(keys)
    down, up = keys[pos]
    keys[pos] = (down, up, down - lead, up + lag, side)
    return keys


def test_features_are_holds_then_press_gaps():
    keys = sample([100, 80, 90], [200, 150])
    assert rhythm.features(keys) == [100, 80, 90, 200, 150]


def test_features_length_is_two_n_minus_one():
    keys = sample([100] * 9, [150] * 8)
    assert len(rhythm.features(keys)) == 17


def test_tempo_runs_from_first_press_to_last_release():
    keys = sample([100, 80, 90], [200, 150], start=50)
    assert rhythm.tempo(keys) == 200 + 150 + 90


def test_identical_samples_score_zero():
    keys = sample([100, 80, 90], [200, 150])
    profile = rhythm.build_profile([keys] * 5)
    assert rhythm.distance(profile, keys) == 0


def test_spread_never_goes_below_the_floor():
    keys = sample([100, 80, 90], [200, 150])
    profile = rhythm.build_profile([keys] * 5)
    assert min(profile["spread"]) == rhythm.MIN_SPREAD


def test_a_different_rhythm_scores_higher():
    mine = [sample([100 + d, 80, 90 - d], [200, 150 + d]) for d in range(-4, 5, 2)]
    profile = rhythm.build_profile(mine)
    close = sample([101, 80, 89], [200, 151])
    far = sample([160, 40, 150], [320, 90])
    assert rhythm.distance(profile, close) < 1 < rhythm.distance(profile, far)


def test_consistent_keys_count_more_than_noisy_ones():
    samples = [sample([100, 80 + d * 10, 90, 110, 95], [200, 150, 170, 160]) for d in range(-2, 3)]
    profile = rhythm.build_profile(samples)
    off_steady = sample([120, 80, 120, 110, 95], [200, 150, 170, 190])
    off_noisy = sample([100, 100, 120, 110, 95], [200, 150, 170, 190])
    assert rhythm.distance(profile, off_steady) > rhythm.distance(profile, off_noisy)


def test_one_slip_is_forgiven():
    mine = [sample([100 + d, 80, 90, 110, 95], [200, 150, 170, 160 - d]) for d in range(-2, 3)]
    profile = rhythm.build_profile(mine)
    slip = sample([100, 80, 90, 110, 95], [200, 450, 170, 160])
    everywhere = sample([130, 105, 120, 140, 125], [240, 190, 210, 200])
    assert rhythm.distance(profile, slip) == 0
    assert rhythm.distance(profile, everywhere) > 2


def test_shift_profile_is_learned():
    base = sample([100, 80, 90], [200, 150])
    profile = rhythm.build_profile([shifted(base, 0, 90, 40)] * 5)
    assert profile["shift"] == [{
        "pos": 0, "lead": 90, "lead_spread": rhythm.MIN_SPREAD,
        "lag": 40, "lag_spread": rhythm.MIN_SPREAD, "side": 0,
    }]


def test_other_shift_key_adds_a_penalty():
    base = sample([100, 80, 90], [200, 150])
    profile = rhythm.build_profile([shifted(base, 0, 90, 40, side=0)] * 5)
    same = rhythm.distance(profile, shifted(base, 0, 90, 40, side=0))
    other = rhythm.distance(profile, shifted(base, 0, 90, 40, side=1))
    assert same == 0
    assert other > 0


def test_capital_without_shift_is_penalised():
    base = sample([100, 80, 90], [200, 150])
    profile = rhythm.build_profile([shifted(base, 0, 90, 40)] * 5)
    assert rhythm.distance(profile, base) > 1


def test_mixed_shift_positions_skip_shift_checks():
    base = sample([100, 80, 90], [200, 150])
    profile = rhythm.build_profile([shifted(base, 0, 90, 40), shifted(base, 1, 90, 40)])
    assert profile["shift"] == []


def test_speed_check_accepts_usual_pace_and_rejects_far_off():
    samples = [sample([100] * 5, [200 + d] * 4) for d in range(-10, 11, 4)]
    profile = rhythm.build_profile(samples * 2)
    assert rhythm.tempo_ok(profile, sample([100] * 5, [200] * 4))
    assert not rhythm.tempo_ok(profile, sample([50] * 5, [100] * 4))
    assert not rhythm.tempo_ok(profile, sample([200] * 5, [400] * 4))


def test_new_profiles_get_more_speed_leeway():
    keys = sample([100] * 5, [200] * 4)
    young = rhythm.build_profile([keys] * (rhythm.SETTLED_PROFILE - 1))
    settled = rhythm.build_profile([keys] * rhythm.SETTLED_PROFILE)
    usual = rhythm.tempo(keys)
    edge = sample([100] * 5, [200 * 1.45] * 4)
    assert rhythm.tempo(edge) - usual > rhythm.TEMPO_LIMIT * settled["tempo_spread"]
    assert not rhythm.tempo_ok(settled, edge)
    assert rhythm.tempo_ok(young, edge)


def test_drop_outliers_removes_a_distracted_sample():
    good = [sample([100 + d, 80, 90], [200, 150 - d]) for d in range(-3, 4)]
    distracted = sample([400, 300, 90], [900, 600])
    kept = rhythm.drop_outliers(good + [distracted])
    assert distracted not in kept
    assert len(kept) >= 6


def test_drop_outliers_keeps_at_least_six():
    wild = [sample([100 * k, 80], [200 * k]) for k in range(1, 9)]
    assert len(rhythm.drop_outliers(wild)) >= 6


def test_auto_threshold_has_a_floor_for_very_steady_typists():
    samples = [sample([100, 80], [200])] * 8
    assert rhythm.auto_threshold(samples) == 1.5


def test_auto_threshold_stays_in_range():
    wild = [sample([100 * 2 ** i, 80], [200 + 50 * i]) for i in range(8)]
    assert 1.5 <= rhythm.auto_threshold(wild) <= 4.0
