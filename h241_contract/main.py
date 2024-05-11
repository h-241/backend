from __future__ import annotations

from pydantic import BaseModel


class ServiceProvider(BaseModel):
    name: str
    bio: str
    executions: list[Execution]
    
    recovery_email: str
    icp_ledger_account_id: str
    min_service_request_duration: int
    min_service_request_price: int


class ServiceRequest(BaseModel):
    job_description: str
    
    max_duration_to_match_with_service_provider_until_expiration: int
    max_duration_for_service_provider_to_complete_work_until_expiration: int
    
    completion_criteria: str
    payment_criteria: str
    max_price: int
    min_price: int


class Execution(BaseModel):
    # we'll carbon copy this onto the chain
    
    service_request: ServiceRequest
    service_provider: ServiceProvider
    start_time_ns: int
    end_time_ns: int
    
    amount_reserved: int
    reservation_taken_from_icp_ledger_account_id: str
    amount_paid: int
    paid_to_icp_ledger_account_id: str
    
    duration: int

# TODO: also make it possible to see the realtime threads of all available jobs

# TODO: most of this goes on the couchbase. only a small amount on the icp. maybe we don't store anything on the icp. just use it for tokenomics

# TODO: make the execution have a chat betwen the provider and consumer. the completion criteria can require the human to approve the work (not that this is not the only payment scheme, and it isn't in the core idea of blockchain so many will not do this)

def request_service(service_request, requester) -> ServiceRequest:
    ...

def present_evidence_of_completion(evidence, service_request, service_provider) -> ServiceRequest:
    ...

def register_service_provider(service_provider) -> ServiceProvider:
    ...

def cancel_execution(execution) -> Execution:
    ...

