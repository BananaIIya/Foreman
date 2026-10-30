"""Continuous expert allowance with the current 28% savings target."""
from dataclasses import fields
from v9_runtime.policy import Budget
from v39_runtime.coordinator import Coordinator as PreviousCoordinator


class Coordinator(PreviousCoordinator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.budget = Budget(**{field.name: getattr(self.budget, field.name)
                                for field in fields(Budget)})
