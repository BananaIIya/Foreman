"""Change only the execution allowance to diagnose premature cost truncation."""
from v20_runtime.coordinator import Coordinator as PreviousCoordinator

class Coordinator(PreviousCoordinator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Diagnostic execution cap, NOT the external30%savings acceptance rule.
        self.budget.savings_target = .10
