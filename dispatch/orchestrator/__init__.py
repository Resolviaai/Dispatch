"""Dispatch Orchestrator Package.
Provides persistent job queue, discrete stage machine, lease heartbeats, and crash recovery.
"""
from dispatch.orchestrator.state_machine import PipelineStage, JobStatus, get_next_stage
from dispatch.orchestrator.job_queue import (
    enqueue_job,
    claim_job,
    renew_heartbeat,
    complete_stage_checkpoint,
    fail_stage_job,
    init_job_queue_schema
)
from dispatch.orchestrator.recovery import recover_laptop_orchestrator

__all__ = [
    "PipelineStage",
    "JobStatus",
    "get_next_stage",
    "enqueue_job",
    "claim_job",
    "renew_heartbeat",
    "complete_stage_checkpoint",
    "fail_stage_job",
    "init_job_queue_schema",
    "recover_laptop_orchestrator"
]
