import time
from collections import defaultdict

class ApiBudgeter:
    def __init__(self, max_cpm:int=600):
        self.max_cpm = max_cpm
        self.calls = 0
        self.minute_start = time.time()
        self.buckets = defaultdict(int)

    def _maybe_reset(self):
        if time.time() - self.minute_start >= 60:
            self.calls = 0
            self.minute_start = time.time()
            self.buckets = defaultdict(int)

    def tick(self, n=1, cat="other"):
        self._maybe_reset()
        self.calls += n
        self.buckets[cat] += n

    def remaining(self):
        self._maybe_reset()
        return max(0, self.max_cpm - self.calls)

    def summary(self):
        return f"{self.calls}/{self.max_cpm} pos:{self.buckets['pos']} rot:{self.buckets['rot']}"
