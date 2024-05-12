from __future__ import annotations

from pydantic import UUID4, BaseModel


class Worker(BaseModel):
    name: str
    bio: str
    executions: list[Task]
    
    recovery_email: str
    icp_ledger_account_id: str
    
    min_task_duration: int
    min_task_price: int


class TaskRequest(BaseModel):
    description: str
    
    match_expiration_duration: int
    completion_expiration_duration: int
    
    max_price: int
    min_price: int


class Task(BaseModel):
    # we'll carbon copy this onto the chain
    
    task_request: TaskRequest
    task_provider: Worker
    start_time_ns: int
    end_time_ns: int
    
    amount_escrowed: int
    escrow_source: str
    amount_paid: int
    paid_to_icp_ledger_account_id: str
    
    duration: int

# How do i authenticate identifty?

def create_task_request(task_request_data: TaskRequest) -> UUID4:
    ...

def get_available_task_requests(task_provider_id: UUID4) -> list[TaskRequest]:
    ...

def accept_task_request(task_request_id: UUID4) -> UUID4:
    ...

def add_message_to_task(task_id: UUID4, message: str):
    ...

def get_messages_for_task(task_id: UUID4) -> list[str]:
    ...

def cancel_task(task_id: UUID4):
    ...

