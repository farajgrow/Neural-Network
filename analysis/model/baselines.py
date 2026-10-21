"""Simple baselines the GRU has to beat.

Next step = the scammer's next move (each returns a probability for every move, so log
loss is comparable). Examples without a later move are ignored when fitting.
- MostFrequentNext: the training set's next-move distribution, ignoring the input.
- MarkovNext(order=1): "most common next move given the last step" (what the app does
  today), optionally per scam type. The context is the last input step, exactly what
  the GRU sees, so it can be a reaction such as victim_suspicious.
- MarkovNext(order=2): the same given the last two steps.

Higher-order or per-type counts fall back to the simpler model with additive "backoff"
smoothing: P(next | context) = (count(context, next) + B * P_simpler(next)) / (count(context) + B).
With no data for a context this is exactly the simpler model; with lots of data it is the
raw frequency.

Loss risk:
- BaseRateLoss: the training set's share of lost-money prefixes.
- LogisticLoss: logistic regression on which steps have been seen so far + scam type.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import numpy as np

from analysis.model.data import Example, Vocab

BACKOFF = 1.0     # pseudo-count B given to the simpler model
START = "<start>"


class MostFrequentNext:
    """Predicts the overall next-step distribution of the training set."""

    def __init__(self, vocab: Vocab, smoothing: float = 0.5):
        self.vocab = vocab
        self.smoothing = smoothing

    def fit(self, examples: list[Example]) -> "MostFrequentNext":
        counts = Counter(e.next_step for e in examples if e.next_step is not None)
        raw = np.array([counts[t] for t in self.vocab.targets], dtype=float) + self.smoothing
        self.probs = raw / raw.sum()
        return self

    def predict_proba(self, examples: list[Example]) -> np.ndarray:
        return np.tile(self.probs, (len(examples), 1))


class MarkovNext:
    """Next step from the last `order` steps, optionally per scam type, with backoff.

    Backoff chain for order=2, by_type=True (most specific first):
        (type, prev, last) -> (prev, last) -> (type, last) -> (last) -> overall distribution
    """

    def __init__(self, vocab: Vocab, order: int = 1, by_type: bool = False, backoff: float = BACKOFF):
        self.vocab = vocab
        self.order = order
        self.by_type = by_type
        self.backoff = backoff

    def _contexts(self, e: Example) -> list[tuple]:
        """Contexts from simplest to most specific."""
        padded = (START,) * self.order + e.prefix
        contexts = []
        for n in range(1, self.order + 1):
            history = padded[-n:]
            contexts.append(("all",) + history)
            if self.by_type:
                contexts.append((e.scam_type,) + history)
        return contexts

    def fit(self, examples: list[Example]) -> "MarkovNext":
        self.prior = MostFrequentNext(self.vocab).fit(examples).probs
        self.counts: dict[tuple, Counter] = defaultdict(Counter)
        for e in examples:
            if e.next_step is None:
                continue
            for context in self._contexts(e):
                self.counts[context][e.next_step] += 1
        return self

    def _proba(self, e: Example) -> np.ndarray:
        probs = self.prior
        for context in self._contexts(e):
            counts = self.counts.get(context, Counter())
            raw = np.array([counts[t] for t in self.vocab.targets], dtype=float)
            probs = (raw + self.backoff * probs) / (raw.sum() + self.backoff)
        return probs

    def predict_proba(self, examples: list[Example]) -> np.ndarray:
        return np.array([self._proba(e) for e in examples])


class BaseRateLoss:
    """Predicts the training set's loss rate for every prefix."""

    def fit(self, examples: list[Example]) -> "BaseRateLoss":
        self.rate = float(np.mean([e.lost for e in examples if e.lost is not None]))
        return self

    def predict(self, examples: list[Example]) -> np.ndarray:
        return np.full(len(examples), self.rate)


class LogisticLoss:
    """Logistic regression on a bag of steps seen so far plus a one-hot scam type.

    Fitted with full-batch gradient descent and an L2 penalty (bias not penalised).
    """

    def __init__(self, vocab: Vocab, l2: float = 1e-2, lr: float = 0.5, epochs: int = 2000):
        self.vocab = vocab
        self.l2 = l2
        self.lr = lr
        self.epochs = epochs

    def features(self, examples: list[Example]) -> np.ndarray:
        """One row per example: [input step seen? x13, scam type one-hot x6]."""
        x = np.zeros((len(examples), len(self.vocab.steps) + len(self.vocab.scam_types)))
        for i, e in enumerate(examples):
            for step in e.prefix:
                x[i, self.vocab.step_id(step) - 1] = 1.0
            x[i, len(self.vocab.steps) + self.vocab.type_id(e.scam_type)] = 1.0
        return x

    def fit(self, examples: list[Example]) -> "LogisticLoss":
        known = [e for e in examples if e.lost is not None]
        x = self.features(known)
        y = np.array([e.lost for e in known], dtype=float)
        self.w = np.zeros(x.shape[1])
        self.b = 0.0
        for _ in range(self.epochs):
            p = 1 / (1 + np.exp(-(x @ self.w + self.b)))
            self.w -= self.lr * (x.T @ (p - y) / len(y) + self.l2 * self.w)
            self.b -= self.lr * float(np.mean(p - y))
        return self

    def predict(self, examples: list[Example]) -> np.ndarray:
        return 1 / (1 + np.exp(-(self.features(examples) @ self.w + self.b)))
