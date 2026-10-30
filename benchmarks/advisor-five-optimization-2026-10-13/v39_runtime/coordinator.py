"""Target 28% savings with margin above the human's 25% per-success floor."""
from v34_runtime.coordinator import Coordinator as PreviousCoordinator


class Coordinator(PreviousCoordinator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.budget.savings_target = .28
