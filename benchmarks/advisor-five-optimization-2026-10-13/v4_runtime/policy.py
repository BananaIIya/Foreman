"""Task-independent evidence preparation and executable specialist handoff."""
import copy
from dataclasses import dataclass
from pathlib import PurePosixPath
from routing import Budget as BaseBudget, DECISION_SCHEMA as BASE_SCHEMA, WORKER_SYSTEM, evidence_fingerprint

DISCOVER='''Inspect the original task and existing files using at most two batched tool calls. Do not implement a speculative difficult algorithm yet. Identify what you can implement reliably and the single uncertain decision needing specialist reasoning. Collect a compact, factual handoff: exact relevant paths, input formats, any already available intermediate data, and observable acceptance checks. Do not dump whole large files. Return JSON with status (ready or needs_advisor), plan, critical_question, evidence, relevant_paths, acceptance_checks. No human approval, delegation or benchmark-solution search.'''
PREPARE='''Prepare the specific missing evidence for a specialist. You remain the executing worker; do routine parsing, reproduction, input normalization or concise measurements yourself. Materialize runnable intermediate artifacts and check their consistency against original inputs. Do not guess the specialist answer. Use at most four batched tool calls and stop if blocked. Return JSON with status, evidence, relevant_paths, checks, critical_question, and unresolved_assumptions. Prefer existing original files when they already provide sufficient evidence.'''
WORKER='''You own implementation, integration and delivery. The controller may already have installed exact specialist-authored files. Inspect those actual files, preserve supported corrections, finish the remaining task and run the supplied validation commands plus meaningful checks against the original requirements. A specialist code patch is unverified until executed; do not assume it passed. Do not translate it into a different algorithm without evidence. If the specialist requested context instead, collect that exact evidence and stop at the next checkpoint. No human approval or hidden grader is available. Return JSON with status (completed or needs_advisor), summary, relevant_paths, checks with actual outputs, critical_question and unresolved_assumptions. Never claim an unrun check or hide incomplete outputs. No delegation or benchmark-solution search.'''
DECISION='''You route work using the original task, current public evidence and actual workspace excerpts. You are not the solver and have no hidden grader. At discovery, send a concrete supported plan to implementation; escalate an uncertain specialist assumption before costly speculative implementation. Before escalation, check handoff readiness: does the advisor have the exact source, representative input and observed failure needed to return a complete executable correction without exploring the workspace? Existing source files and original images can already be sufficient. If cheap routine derivation/reproduction is missing, select prepare ONCE and specify those exact artifacts/checks. Do not require a full solution or needless preprocessing; consider its cost relative to the remaining budget. After one preparation, send an honest bounded question with the evidence actually available. At delivery, require semantic checks, not just file existence. One straightforward missing check may return to worker; repeated uncertainty should escalate. Do not re-escalate unchanged verified issues. The advisor has NO tools and must return complete replacement files or request specific missing evidence. Write one focused intervention contract with source paths and acceptance evidence, leaving routine integration to the worker. No human approval, hidden tests, reference answers, or historical solutions. Return JSON only.'''
ADVISOR='''You are a specialist supplying executable corrections to an executing worker. You have NO tools or filesystem access: all available evidence is in the payload. Do not claim to read, run, modify or validate anything. Reason from the original task, attached original images, supplied actual source and observations. Return JSON only matching the protocol: status (patch or needs_context or blocked), files (full replacement file contents with absolute /app paths), validation_commands (commands the worker should run), requested_evidence (specific missing observations), and handoff (interface/invariants to preserve). Provide the smallest complete critical implementation, including a full function/script when that is the indivisible hard part. This must be runnable code, not vague prose advice or pseudocode. Include all essential imports and preserve unrelated correct behavior. The controller will write valid files literally; the worker will execute checks and finish integration. If crucial evidence is missing, request it in one concise batch instead of fabricating values. Do not use reference solutions or hidden tests. Keep explanation short; return no unrelated rewrites.'''
DECISION_SCHEMA=copy.deepcopy(BASE_SCHEMA)
WORKER+=' When a check fails, distinguish an implementation defect from a mistaken test expectation using an explicit original requirement. Never weaken a test merely to make it pass. Include a compact requirement-to-check coverage table and any uncovered edge cases in the final evidence.'
WORKER+=' After an executable specialist patch, batch installation, execution and semantic checks into at most three tool calls. If it fails, report the exact error and relevant source at the checkpoint instead of repeatedly rewriting the difficult part.'
ADVISOR+=' For a generated-artifact task, provide a runnable generator for the complete critical artifact, rather than a geometry/formula fragment that forces the worker to reconstruct the hard implementation. Leave dependency installation, invoking the generator and checking actual outputs to the worker.'
DECISION+=' Treat self-authored passing tests as evidence, not proof. Look for requirements absent from the coverage table and check whether changes to failed test expectations were justified by the original specification; do not accept circular validation.'
DECISION+=' The original_image_attachments manifest confirms actual images attached to this request and to the Sol advisor request. An image is not missing merely because it is absent from text excerpts. Do not send the worker to re-fetch, raster-decode, or derive the expert interpretation of an already attached drawing. Preparation may gather routine observations only, never solve the very specialist uncertainty that triggered escalation. Missing future output artifacts are normal before implementation. Escalate directly when the original inputs already suffice for the expert.'
PREPARE+=' Do not infer difficult geometry, scientific annotations, topology or signal interpretation for the specialist. If the contract asks for that, return needs_advisor immediately with the available original evidence. Keep your report under 1200 characters; preserve exact errors and relevant paths.'
DECISION_SCHEMA['properties']['action']['enum'].append('prepare')
PATCH_SCHEMA={'type':'object','additionalProperties':False,'required':['status','files','validation_commands','requested_evidence','handoff'],'properties':{
 'status':{'type':'string','enum':['patch','needs_context','blocked']},
 'files':{'type':'array','items':{'type':'object','additionalProperties':False,'required':['path','content'],'properties':{'path':{'type':'string'},'content':{'type':'string'}}}},
 'validation_commands':{'type':'array','items':{'type':'string'}},
 'requested_evidence':{'type':'array','items':{'type':'string'}},'handoff':{'type':'string'}}}

@dataclass
class Budget(BaseBudget):
 preparations:int=0
 worker_phase_floor:float=0.0
 def reserve(self,worker_model):
  return min(self.baseline*.15,.025) if 'luna' in worker_model else self.baseline*.15
 def integration_cap(self,worker_model):
  # One integration opportunity survives an advisor overshoot. Every cent counts.
  return max(self.reserve(worker_model), self.ceiling-self.spent if self.usage_complete else 0)
 def preparation_allowance(self,worker_model):
  if not self.usage_complete:return 0.0
  available=max(0.0,min(self.baseline*.10,self.ceiling-self.spent-self.baseline*.45-self.reserve(worker_model)))
  return available if available>=self.worker_phase_floor else 0.0

def route(decision,stage,budget,history,worker_model='gpt-6-luna'):
 if not budget.can_continue():return 'budget_exhausted'
 action=decision['action']
 if action=='deliver' and stage!='delivery':return 'continue'
 if action=='prepare' and budget.preparations>=1:action='advisor'
 if action=='prepare' and budget.preparation_allowance(worker_model)<=0:action='advisor'
 if action=='self_check' and budget.self_checks>=1:action='advisor'
 if action=='advisor':
  if evidence_fingerprint(decision) in history:return 'repeated_issue'
  if budget.advisor_allowance()<=0:return 'budget_exhausted'
 return action

def validate_patch(patch):
 import jsonschema
 jsonschema.validate(patch,PATCH_SCHEMA)
 if patch['status']!='patch':
  if patch['files']:raise ValueError('Non-patch response cannot modify files')
  return
 if not patch['files']:raise ValueError('Patch needs complete files')
 seen=set();total=0
 for file in patch['files']:
  p=PurePosixPath(file['path'])
  if not p.is_absolute() or '..' in p.parts or not str(p).startswith('/app/'):
   raise ValueError('Patch path must stay in the task workspace')
  if any(x in p.parts for x in ('.cursor','.git','.agents')):raise ValueError('Cannot modify orchestration configuration')
  if str(p) in seen:raise ValueError('Duplicate patch path')
  seen.add(str(p));total+=len(file['content'].encode())
 if total>400000:raise ValueError('Specialist patch exceeds bounded handoff size')
