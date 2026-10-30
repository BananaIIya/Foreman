"""Task-independent routing and cost accounting for bounded specialist interventions.

The gate only sees task inputs, public execution evidence and current artifacts.
The hidden verifier runs after this state machine is terminal.
"""
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json


DISCOVER = """Inspect the task inputs and existing project, then stop at a design checkpoint BEFORE implementing the difficult part. Use at most three focused tool calls. Do not spend this turn solving the whole task. Identify the concrete implementation you can do reliably and the single highest-risk assumption that needs expert verification. A coordinator can automatically consult a specialist; no human approval is needed.
Return a concise JSON object with: status ('ready' or 'needs_advisor'), plan, critical_question, evidence (actual observations), relevant_paths, acceptance_checks. Keep the response under 1200 words. This is a short discovery stage; avoid prolonged speculative reasoning."""

WORKER_SYSTEM = """You are the worker in an automated implementation pipeline. Follow the current turn's stage-specific instructions: discovery is a short inspection checkpoint; implementation performs the changes and checks. The stage can change in later turns. Consult the coordinator by returning a focused needs_advisor report when blocked; no human approval is needed. Use only this task workspace and the supplied original task. Never search for benchmark solutions or delegate to additional agents."""

WORKER = """You own implementation and delivery. Continue from the current workspace and the coordinator's handoff. A specialist may have edited the critical implementation directly: inspect those changes, preserve the verified algorithm, integrate it and run meaningful checks against the original task. Do not reinterpret a validated correction without contradictory evidence.
If you encounter a hard uncertainty, a repeated failure, or conflicting evidence, stop this turn early with status needs_advisor and a focused question. Do not wait for an external grader. Do not request human approval. Keep the scope to the original task. Do not delegate or search for benchmark solutions.
At this checkpoint return JSON with status ('completed' or 'needs_advisor'), summary, critical_question, relevant_paths, checks (command, observed result, and requirement tested), unresolved_assumptions. An exported file or exit code alone does not prove semantic correctness. Never invent reference values or conceal an unmet requirement. Prefer a few batched, decisive checks over repeated unstructured attempts."""

DECISION = """You are a cheap routing reviewer, not the solver. Use only the supplied original task, current worker's observations, public command outputs and current artifacts. No hidden score or reference solution is available.
At discovery, identify the single critical design assumption or domain interpretation most likely to invalidate the result. Escalate before implementation if the proposed plan relies on a materially uncertain specialist inference. A concrete, well-supported plan may proceed without an advisor. Avoid demanding proof of routine steps that have not happened yet.
At delivery, compare each substantive requirement with the evidence. Check correctness of content, not merely file existence or syntactic validity. A concrete failed invariant or an unverified specialist assumption warrants intervention. A straightforward missing check can go back to the worker once. If the same concern survives a self-check, escalate instead of looping.
Do not re-escalate an already verified issue without new contradictory evidence. Ignore process restrictions already enforced by the harness. Never require hidden tests. Write a bounded intervention contract: one specific uncertainty, the necessary workspace paths, the exact implementation responsibility transferred, and observable acceptance evidence. The advisor may inspect files, run tools and directly repair that critical part. The worker retains integration and final verification. No human approval gate.
Return the required JSON only. Be concise; do not attempt a complete solution yourself."""

ADVISOR = """You are the specialist in a worker/advisor pipeline. You have the same task workspace and may inspect its actual files, use tools, and directly repair the critical implementation specified in the intervention contract. Your job is to remove the named uncertainty with executable evidence, not to leave the worker another ambiguous prose plan.
Read the relevant original inputs yourself, including images or data when needed. Check the worker's assumptions independently. Implement the smallest complete critical correction, including a full function or script when that is the indivisible difficult part. Run a decisive local check of it. Keep unrelated setup, formatting, integration and delivery to the worker. Do not rewrite correct work, delegate, or fetch benchmark solutions. Do not access hidden verifiers or historical runs.
Work within the supplied cost target. Prefer batched inspection and computation, avoid speculative alternative implementations, and stop once the named uncertainty is resolved. If you cannot resolve it, report the specific remaining evidence needed; do not fabricate confidence.
Return a compact handoff: root cause, files actually changed, exact interface and invariants the worker must preserve, commands actually executed with observed results, and remaining worker actions. A theoretical explanation without a usable correction is insufficient when the worker has already failed to implement it."""

DECISION_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['action', 'issue', 'evidence', 'paths', 'intervention', 'acceptance'],
    'properties': {
        'action': {'type': 'string', 'enum': ['continue', 'self_check', 'advisor', 'deliver']},
        'issue': {'type': 'string'}, 'evidence': {'type': 'string'},
        'paths': {'type': 'array', 'items': {'type': 'string'}},
        'intervention': {'type': 'string'}, 'acceptance': {'type': 'string'},
    },
}

PRICES = {
    'gpt-6-luna': (Decimal('.10'), Decimal('.01'), Decimal('.125'), Decimal('.50')),
    'gpt-6.1-sol': (Decimal('2'), Decimal('.10'), Decimal('2.50'), Decimal('10')),
    'grok-4.7': (Decimal('2'), Decimal('.50'), None, Decimal('6')),
}


def usage_cost(model, usage, provider):
    """Convert native disjoint/inclusive counters correctly; missing is unknown.

    Codex input includes cache reads/writes. Cursor input is a disjoint bucket.
    Reasoning tokens are already part of output and must never be added twice.
    Call this per response to apply context-based pricing, not to session totals.
    """
    keys = (['inputTokens', 'cachedInputTokens', 'cacheWriteInputTokens', 'outputTokens']
            if provider == 'codex' else ['inputTokens', 'cacheReadTokens', 'cacheWriteTokens', 'outputTokens'])
    if not all(isinstance(usage.get(k), (int, float)) and usage[k] >= 0 for k in keys):
        return None
    inp, cached, writes, out = [Decimal(str(usage[k])) for k in keys]
    fresh = inp - cached - writes if provider == 'codex' else inp
    if fresh < 0:
        raise ValueError('Inconsistent inclusive input counters')
    p_in, p_cache, p_write, p_out = PRICES[model]
    if writes and p_write is None:
        return None
    if provider == 'codex' and inp > 272000:
        p_in, p_cache, p_write, p_out = p_in * 2, p_cache * 2, p_write * 2, p_out * Decimal('1.5')
    return float((fresh*p_in + cached*p_cache + writes*(p_write or 0) + out*p_out)/1000000)


@dataclass
class Budget:
    baseline: float
    spent: float = 0.0
    savings_target: float = .30
    advisor_calls: int = 0
    self_checks: int = 0
    usage_complete: bool = True

    @property
    def ceiling(self):
        return self.baseline * (1-self.savings_target)

    def record(self, cost):
        if cost is None:
            self.usage_complete = False
        else:
            self.spent += cost

    def advisor_allowance(self):
        # Reserve 15% of solo cost for worker integration and the delivery gate.
        if not self.usage_complete or self.advisor_calls >= 2:
            return 0.0
        return max(0.0, min(self.baseline*.45, self.ceiling-self.spent-self.baseline*.15))

    def can_continue(self):
        return self.usage_complete and self.spent < self.ceiling


def evidence_fingerprint(decision):
    return sha256(json.dumps({k: decision[k] for k in ['issue', 'evidence', 'paths']},
                             sort_keys=True).encode()).hexdigest()


def choose_action(decision, stage, budget, previous_advisor_evidence=()):
    action = decision['action']
    if not budget.can_continue():
        return 'budget_exhausted'
    if action == 'deliver' and stage != 'delivery':
        return 'continue'
    if action == 'self_check' and budget.self_checks >= 1:
        action = 'advisor'
    if action == 'advisor':
        if evidence_fingerprint(decision) in previous_advisor_evidence:
            return 'repeated_issue'
        if budget.advisor_allowance() <= 0:
            return 'budget_exhausted'
    return action
