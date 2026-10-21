"""Tests for analysis/model. Torch-only tests are skipped when torch is not installed."""

import math

import numpy as np
import pytest

from analysis.graphs import OUTCOME_STEPS, load_vocab
from analysis.model.baselines import BaseRateLoss, LogisticLoss, MarkovNext, MostFrequentNext
from analysis.model.data import (END, Story, holdout_by_story, kfold_by_story, load_dataset, make_vocab,
                                 prefix_examples, to_story)
from analysis.model.metrics import calibration_table, roc_auc, topk_accuracy

RAW_VOCAB = load_vocab()
VOCAB = make_vocab(RAW_VOCAB)


def raw_story(sid, steps, outcome="unknown", scam_type="fake_job"):
    return {"id": sid, "source": "reddit", "scam_type": scam_type, "outcome": outcome,
            "events": [{"step": s, "actor": "scammer", "detail": "x"} for s in steps],
            "amount_lost_usd": None, "snippet": "x"}


def story(sid, steps, lost=None, scam_type="fake_job"):
    return Story(sid, scam_type, tuple(steps), lost)


# --- data -------------------------------------------------------------------------------

def test_outcome_steps_are_not_inputs_or_targets():
    raw = raw_story("a", ["initial_contact", "small_payment", "money_lost", "small_payment", "victim_exits"],
                    "lost_money")
    s = to_story(raw, VOCAB, RAW_VOCAB)
    # money_lost removed, and the two small_payments it separated collapse into one
    assert s.script == ("initial_contact", "small_payment")
    assert s.lost == 1
    assert not OUTCOME_STEPS & set(VOCAB.steps)
    assert not OUTCOME_STEPS & set(VOCAB.targets)


def test_no_outcome_steps_in_real_data_inputs():
    stories, _ = load_dataset()
    for e in prefix_examples(stories):
        assert not OUTCOME_STEPS & set(e.prefix)
        assert e.next_step not in OUTCOME_STEPS


def test_prefix_generation():
    examples = prefix_examples([story("a", ["initial_contact", "trust_building", "small_payment"], lost=1)])
    assert [(e.prefix, e.next_step) for e in examples] == [
        (("initial_contact",), "trust_building"),
        (("initial_contact", "trust_building"), "small_payment"),
        (("initial_contact", "trust_building", "small_payment"), END),
    ]
    assert all(e.lost == 1 for e in examples)


def test_unknown_outcome_gives_no_loss_label():
    s = to_story(raw_story("a", ["initial_contact"], "unknown"), VOCAB, RAW_VOCAB)
    assert s.lost is None
    assert to_story(raw_story("b", ["initial_contact"], "escaped"), VOCAB, RAW_VOCAB).lost == 0


def test_folds_never_share_a_story_and_cover_everything():
    stories, _ = load_dataset()
    folds = kfold_by_story(stories, k=5, seed=0)
    test_ids = [s.id for _, test in folds for s in test]
    assert sorted(test_ids) == sorted(s.id for s in stories)          # each story tested exactly once
    for train, test in folds:
        assert not {s.id for s in train} & {s.id for s in test}
        fit, val = holdout_by_story(train)
        assert not {s.id for s in fit} & {s.id for s in val}
        assert not {s.id for s in val} & {s.id for s in test}
    assert kfold_by_story(stories, seed=0)[0][1] == kfold_by_story(stories, seed=0)[0][1]   # deterministic


# --- baselines ----------------------------------------------------------------------------

def test_markov_baseline_on_tiny_dataset():
    a, b, c = "initial_contact", "trust_building", "small_payment"
    stories = [story("1", [a, b]), story("2", [a, b]), story("3", [a, c]),
               story("4", [a, c], scam_type="romance"), story("5", [a, c], scam_type="romance")]
    examples = prefix_examples(stories)
    query = prefix_examples([story("q", [a], scam_type="fake_job")])[:1]

    markov = MarkovNext(VOCAB, order=1, backoff=0.0).fit(examples)
    probs = markov.predict_proba(query)[0]
    assert probs[VOCAB.target_id(c)] == pytest.approx(3 / 5)   # a -> c in 3 of 5 stories
    assert probs[VOCAB.target_id(b)] == pytest.approx(2 / 5)

    by_type = MarkovNext(VOCAB, order=1, by_type=True, backoff=0.0).fit(examples)
    probs = by_type.predict_proba(query)[0]
    assert probs[VOCAB.target_id(b)] == pytest.approx(2 / 3)   # within fake_job: a -> b in 2 of 3
    assert VOCAB.targets[int(np.argmax(probs))] == b

    # every story ends after its second step
    after_b = by_type.predict_proba(prefix_examples([story("q", [a, b])])[1:])[0]
    assert VOCAB.targets[int(np.argmax(after_b))] == END


def test_markov_backs_off_for_unseen_context():
    a, b = "initial_contact", "trust_building"
    examples = prefix_examples([story("1", [a, b]), story("2", [a, b])])
    model = MarkovNext(VOCAB, order=2, by_type=True).fit(examples)
    unseen = prefix_examples([story("q", ["withdrawal_blocked"], scam_type="romance")])
    np.testing.assert_allclose(model.predict_proba(unseen)[0], MostFrequentNext(VOCAB).fit(examples).probs)


def test_baseline_probabilities_sum_to_one():
    stories, _ = load_dataset()
    examples = prefix_examples(stories[:100])
    for model in (MostFrequentNext(VOCAB), MarkovNext(VOCAB), MarkovNext(VOCAB, order=2, by_type=True)):
        np.testing.assert_allclose(model.fit(examples).predict_proba(examples).sum(axis=1), 1.0)


def test_loss_baselines_ignore_unknown_outcomes():
    examples = prefix_examples([story("1", ["small_payment"], 1), story("2", ["victim_suspicious"], 0),
                                story("3", ["small_payment"], None), story("4", ["small_payment"], None)])
    assert BaseRateLoss().fit(examples).rate == 0.5
    p = LogisticLoss(VOCAB).fit(examples).predict(examples[:2])
    assert p[0] > 0.5 > p[1]


# --- metrics ------------------------------------------------------------------------------

def test_metrics_on_hand_examples():
    assert roc_auc(np.array([0.1, 0.4, 0.35, 0.8]), np.array([0, 0, 1, 1])) == 0.75
    assert roc_auc(np.array([0.5, 0.5]), np.array([0, 1])) == 0.5
    probs = np.array([[0.6, 0.3, 0.1], [0.2, 0.3, 0.5]])
    assert topk_accuracy(probs, np.array([1, 2]), 1) == 0.5
    assert topk_accuracy(probs, np.array([1, 2]), 2) == 1.0
    table = calibration_table(np.array([0.05, 0.15, 0.95]), np.array([0, 1, 1]), bins=5)
    assert table[0]["n"] == 2 and table[0]["observed"] == 0.5 and table[4]["n"] == 1


# --- GRU (needs torch) --------------------------------------------------------------------

def test_gru_forward_shape_and_masked_loss():
    torch = pytest.importorskip("torch")
    from analysis.model.gru import GRUConfig, ScamGRU, encode_stories, masked_losses

    stories = [story("1", ["initial_contact", "trust_building", "small_payment"], lost=1),
               story("2", ["initial_contact"], lost=None)]
    batch = encode_stories(stories, VOCAB)
    model = ScamGRU(len(VOCAB.steps), len(VOCAB.scam_types), len(VOCAB.targets), GRUConfig()).eval()
    next_logits, loss_logits = model(batch["steps"], batch["types"])
    assert next_logits.shape == (2, 3, len(VOCAB.targets))
    assert loss_logits.shape == (2, 3)
    assert batch["mask"].sum() == 4                      # 3 + 1 real positions
    assert batch["loss_mask"].sum() == 3                 # only story 1 has a known outcome

    # Changing the unknown story's loss logits must not change the loss-head term.
    _, risk = masked_losses(next_logits, loss_logits, batch)
    tweaked = loss_logits.clone()
    tweaked[1] += 100.0
    _, risk_tweaked = masked_losses(next_logits, tweaked, batch)
    assert torch.isclose(risk, risk_tweaked)

    # A batch with no known outcomes contributes zero loss-head loss.
    unknown = encode_stories([stories[1]], VOCAB)
    with torch.no_grad():
        _, risk_unknown = masked_losses(*model(unknown["steps"], unknown["types"]), unknown)
    assert risk_unknown.item() == 0.0


def test_gru_output_at_position_t_equals_prefix_prediction():
    torch = pytest.importorskip("torch")
    from analysis.model.gru import GRUConfig, ScamGRU, encode_stories, predict_examples

    torch.manual_seed(0)
    s = story("1", ["initial_contact", "platform_switch", "trust_building"], lost=0)
    model = ScamGRU(len(VOCAB.steps), len(VOCAB.scam_types), len(VOCAB.targets), GRUConfig()).eval()
    batch = encode_stories([s], VOCAB)
    with torch.no_grad():
        full = torch.softmax(model(batch["steps"], batch["types"])[0][0], dim=-1).numpy()
    per_prefix, _ = predict_examples(model, prefix_examples([s]), VOCAB)
    np.testing.assert_allclose(per_prefix, full, atol=1e-6)


def test_predict_returns_valid_probabilities():
    pytest.importorskip("torch")
    from analysis.model.predict import MODEL_PATH, predict

    if not MODEL_PATH.exists():
        pytest.skip("gru.pt not trained yet (python -m analysis.model.train)")
    result = predict(["initial_contact", "platform_switch", "money_lost"], "pig_butchering")
    probs = result["next_step_probs"]
    assert set(probs) == set(VOCAB.targets)
    assert math.isclose(sum(probs.values()), 1.0, rel_tol=1e-5)
    assert all(0.0 <= p <= 1.0 for p in probs.values())
    assert result["top_next"] == max(probs, key=probs.get)
    assert 0.0 <= result["p_loss"] <= 1.0
    assert predict(["initial_contact"], "not_a_type")["p_loss"] >= 0.0      # falls back to "other"
    with pytest.raises(ValueError):
        predict(["money_lost"], "fake_job")
