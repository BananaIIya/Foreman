from v17_runtime.policy import *
from v15_runtime.policy import INITIAL_WORK as PREVIOUS_INITIAL_WORK

INITIAL_WORK = PREVIOUS_INITIAL_WORK.replace(
    'If work_kind is report_missing_evidence, return a concise needs_advisor report without inventing commands.',
    'If work_kind is report_missing_evidence, distinguish an observation not yet collected from input that is actually unavailable. You may use the same maximum of two focused commands to inspect accessible files and collect the requested mechanical observations. Return a concise needs_advisor report when the observation is unavailable or requires specialist interpretation; never invent an observation or a command result.'
)
PLAN += ''' The work_kind describes the proposed action: reading available files for section names, fields, counts or format anomalies is input_schema_check. report_missing_evidence is for a concrete unavailable prerequisite, not a substitute label for ordinary file inspection. Lack of a previous read is not proof that an input is unavailable.'''
