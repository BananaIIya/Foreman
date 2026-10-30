"""A continuous expert allowance; whole-task ceiling and call bounds unchanged."""
from dataclasses import fields
from v9_runtime.policy import Budget
from v34_runtime.coordinator import Coordinator as PreviousCoordinator


class Coordinator(PreviousCoordinator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.budget = Budget(**{f.name: getattr(self.budget, f.name) for f in fields(Budget)})
