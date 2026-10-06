"""Discrete State Machine for Dispatch Pipeline.
Defines every durable state and transition for jobs, chunks, and clips.
Never infers state from RAM; all state transitions are recorded in SQLite.
"""
from enum import Enum


class PipelineStage(str, Enum):
    VERIFY = "VERIFY"
    TRANSCRIBE = "TRANSCRIBE"
    ANALYZE = "ANALYZE"
    RENDER = "RENDER"
    FINALIZE = "FINALIZE"
    COMPLETED = "COMPLETED"


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    WAITING_FOR_NETWORK = "WAITING_FOR_NETWORK"
    WAITING_FOR_WORKER = "WAITING_FOR_WORKER"
    WAITING_FOR_AI = "WAITING_FOR_AI"
    WAITING_FOR_RESOURCES = "WAITING_FOR_RESOURCES"
    RETRY_PENDING = "RETRY_PENDING"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"


# Valid stage progression order
STAGE_PROGRESSION = [
    PipelineStage.VERIFY,
    PipelineStage.TRANSCRIBE,
    PipelineStage.ANALYZE,
    PipelineStage.RENDER,
    PipelineStage.FINALIZE,
    PipelineStage.COMPLETED
]


def get_next_stage(current_stage: PipelineStage) -> PipelineStage:
    """Return the next stage in the pipeline progression."""
    try:
        idx = STAGE_PROGRESSION.index(current_stage)
        if idx + 1 < len(STAGE_PROGRESSION):
            return STAGE_PROGRESSION[idx + 1]
    except ValueError:
        pass
    return PipelineStage.COMPLETED
