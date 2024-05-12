from __future__ import annotations
import time
from typing import Annotated, Any, Callable, Literal, Optional, Self
from uuid import uuid4
from fastapi import Depends, HTTPException

from pydantic import UUID4, BaseModel, Field


class ModelCreate(BaseModel):
    pass

class ModelUpdate(BaseModel):
    pass

class ModelInDB(BaseModel):
    id: UUID4 = Field(default_factory=uuid4)
    ID = UUID4
    
    _ON_CHAIN_STORAGE: dict[type, dict[ID, Any]] = {}

    @classmethod
    def create(cls, model: ModelCreate) -> Self:
        return cls(id=uuid4(), **model.model_dump())

    @classmethod
    def update(cls, id: UUID4, model: ModelUpdate):
        cls._ON_CHAIN_STORAGE.get(cls, {})[id].update(model.model_dump())
    
    def save(self):
        if not self.id:
            self.id = uuid4()
        self._ON_CHAIN_STORAGE.get(type(self), {})[self.id] = self
    
    @classmethod
    def find_by_id(cls, id: UUID4) -> Self:
        return cls._ON_CHAIN_STORAGE.get(cls, {}).get(id)
    
    @classmethod
    def find_all(cls, skip: int = 0, limit: int = 100) -> list[Self]:
        return list(cls._ON_CHAIN_STORAGE.get(cls, {}).values())[skip:skip+limit]
    
    @classmethod
    def find_all_by_ids(cls, ids: list[UUID4]) -> list[Self]:
        items = [cls.find_by_id(id) for id in ids if cls.find_by_id(id)]
        items = list(filter(lambda item: item is not None, items))
        return items
    
    @classmethod
    def find_all_by_query(cls, query: Callable[[Any], bool], skip: int = 0, limit: int = 100) -> list[Self]:
        return [obj for obj in cls.find_all(skip, limit) if query(obj)]
    
    @classmethod
    def find_or_none(cls, id: UUID4) -> Optional[Self]:
        try:
            return cls.find_by_id(id)
        except ValueError:
            return None
    
    @classmethod
    def delete_by_id(cls, id: UUID4):
        cls._ON_CHAIN_STORAGE.get(cls, {}).pop(id, None)
    
    @classmethod
    def delete_all(cls):
        cls._ON_CHAIN_STORAGE.get(cls, {}).clear()
    
    @classmethod
    def delete_all_by_ids(cls, ids: list[UUID4]):
        for id in ids:
            cls.delete_by_id(id)
    
    def delete(self):
        self._ON_CHAIN_STORAGE.get(type(self), {}).pop(self.id, None)

class UserBase(BaseModel):
    display_name: str
    public_key: str
    icp_ledger_account_id: str

class UserCreate(UserBase):
    pass

class UserUpdate(UserBase):
    pass

class UserInDB(ModelInDB, UserBase):
    created_at: int = Field(default_factory=time.time_ns)
    
    banned: bool = False
    blocked_user_ids: list[UserInDB.ID] = Field(default_factory=list)

    requested_task_ids: list[TaskInDB.ID] = Field(default_factory=list)
    executed_task_ids: list[TaskInDB.ID] = Field(default_factory=list)
    
    min_task_execute_duration: int = 0
    min_task_execute_price: int = 0
    
    def update_profile(self, update_data: UserUpdate):
        self.display_name = update_data.display_name
        self.public_key = update_data.public_key
        self.icp_ledger_account_id = update_data.icp_ledger_account_id
        self.save()

class TaskBase(ModelInDB):
    description: str
    max_price: int
    min_price: int
    initial_escrow_amount: int

class TaskCreate(TaskBase):
    pass

class TaskUpdate(TaskBase):
    pass

class TaskQuery(BaseModel):
    status: Optional[Literal["unassigned", "accepted", "completed", "canceled"]]
    requested_by_id: Optional[UserInDB.ID]
    executed_by_id: Optional[UserInDB.ID]

class TaskInDB(TaskBase):
    requested_by_id: UserInDB.ID
    executed_by_id: Optional[UserInDB.ID] = None
    
    amount_escrowed: int = 0
    amount_paid: int = 0
    
    match_expiration_duration: int = 0
    completion_expiration_duration: int = 0
    
    submitted_time_ns: int = Field(default_factory=time.time_ns)
    accepted_time_ns: Optional[int] = None
    completed_time_ns: Optional[int] = None
    canceled_time_ns: Optional[int] = None
    
    @property
    def status(self) -> Literal["unassigned", "accepted", "completed", "canceled"]:
        if self.canceled_time_ns:
            return "canceled"
        elif self.completed_time_ns:
            return "completed"
        elif self.accepted_time_ns:
            return "accepted"
        else:
            return "unassigned"
    
    messages: list[Message] = Field(default_factory=list)

class Message(BaseModel):
    sender_id: UserInDB.ID
    text: Optional[str] = None
    image: Optional[bytearray] = None

CurrentUser = Annotated[UserInDB, Depends(get_current_user)]

def get_current_user(public_key: str) -> UserInDB:
    user = UserInDB.find_all_by_query(lambda u: u.public_key == public_key)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user[0]

def get_current_identity() -> Identity:
    # TODO: Implement this based on the IC identity system
    pass

def create_user(create_user_data: UserCreate, current_identity: Annotated[Identity, Depends(get_current_identity)]) -> UUID4:
    # authorize that the current_identity is the same as the public_key
    if current_identity.public_key != create_user_data.public_key:
        raise HTTPException(status_code=403, detail="The public_key must match the current_identity")
    
    user = UserInDB.create(create_user_data)
    user.save()
    return user.id

def update_user(update_user_data: UserUpdate, current_user: CurrentUser) -> UUID4:
    current_user.update_profile(update_user_data)
    return current_user.id

@update
def create_task(create_task_data: TaskCreate, current_user: CurrentUser) -> UUID4:
    if current_user.id != create_task_data.requested_by_id:
        raise HTTPException(status_code=403, detail="The current_user does not have permission to create a task for the user with the given id")
    
    task = TaskInDB.create(create_task_data)
    escrow_result = escrow_task_payment(task, create_task_data.initial_escrow_amount)

    if "Ok" in escrow_result:
        task.amount_escrowed = escrow_result["Ok"]
        task.save()
        return task.id
    else:
        raise HTTPException(status_code=400, detail=f"Failed to escrow payment: {escrow_result['Err']}")

def get_available_tasks(query: TaskQuery, current_user: CurrentUser, skip: int = 0, limit: int = 100) -> list[TaskInDB]:
    def task_filter(task: TaskInDB) -> bool:
        if query.status and task.status != query.status:
            return False
        if query.requested_by_id and task.requested_by_id != query.requested_by_id:
            return False
        if query.executed_by_id and task.executed_by_id != query.executed_by_id:
            return False
        if task.requested_by_id in current_user.blocked_user_ids:
            return False
        return True
    
    return TaskInDB.find_all_by_query(task_filter, skip, limit)

@update
def accept_task(task_id: UUID4, current_user: CurrentUser) -> UUID4:
    task = TaskInDB.find_or_none(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="The task does not exist")
    if task.status != "unassigned":
        raise HTTPException(status_code=400, detail="The task is not unassigned")
    if task.requested_by_id in current_user.blocked_user_ids:
        raise HTTPException(status_code=403, detail="You have blocked the requester of this task")
    if task.min_price < current_user.min_task_execute_price:
        raise HTTPException(status_code=403, detail="The task price is below your minimum")
    task.accepted_time_ns = time.time_ns()
    task.executed_by_id = current_user.id
    task.save()
    return task.id

@update
def add_text_message_to_task(task_id: UUID4, text_content: str, current_user: CurrentUser):
    task = TaskInDB.find_or_none(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="The task does not exist")
    if task.status != "accepted":
        raise HTTPException(status_code=400, detail="The task is not in progress")
    if current_user.id != task.requested_by_id and current_user.id != task.executed_by_id:
        raise HTTPException(status_code=403, detail="You are not authorized to add messages to this task")
    message = Message(task_id=task_id, sender_id=current_user.id, text=text_content)
    task.messages.append(message)
    task.save()
    return message.id

@update
def add_image_message_to_task(task_id: UUID4, image_content: bytearray, current_user: CurrentUser):
    task = TaskInDB.find_or_none(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="The task does not exist")
    if task.status != "accepted":
        raise HTTPException(status_code=400, detail="The task is not in progress")
    if current_user.id != task.requested_by_id and current_user.id != task.executed_by_id:
        raise HTTPException(status_code=403, detail="You are not authorized to add messages to this task")
    if len(image_content) > 1024*1024:  # Limit images to 1MB
        raise HTTPException(status_code=400, detail="Image size exceeds the 1MB limit")
    message = Message(task_id=task_id, sender_id=current_user.id, image=image_content)
    task.messages.append(message)
    task.save()
    return message.id

def get_messages_for_task(task_id: UUID4, current_user: CurrentUser) -> list[Message]:
    task = TaskInDB.find_or_none(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="The task does not exist")
    if current_user.id != task.requested_by_id and current_user.id != task.executed_by_id:
        raise HTTPException(status_code=403, detail="You are not authorized to view messages for this task")
    return task.messages

@update
def cancel_task(task_id: UUID4, current_user: CurrentUser):
    task = TaskInDB.find_or_none(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="The task does not exist")
    if task.status != "accepted":
        raise HTTPException(status_code=400, detail="The task is not in progress")
    if current_user.id != task.requested_by_id:
        raise HTTPException(status_code=403, detail="Only the task requester can cancel the task")
    
    refund_result = refund_task_payment(task)
    if "Ok" in refund_result:
        task.canceled_time_ns = time.time_ns()
        task.save()
        return task.id
    else:
        raise HTTPException(status_code=400, detail=f"Failed to refund payment: {refund_result['Err']}")

@update
def complete_task(task_id: UUID4, current_user: CurrentUser):
    task = TaskInDB.find_or_none(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="The task does not exist")
    if task.status != "accepted":
        raise HTTPException(status_code=400, detail="The task is not in progress")
    if current_user.id != task.executed_by_id:
        raise HTTPException(status_code=403, detail="Only the task executor can complete the task")
    
    # evaluate the task completion on the server
    response = # TODO: make an outbound POST web3.h241.com/task_completion_evaluation with {task_id: task.id}
    
    if response.completed:
        amount_to_pay = max(task.min_price, min(response.amount_to_pay, task.max_price))
        payment_result = pay_task_executor(task, amount_to_pay)

        if "Ok" in payment_result:
            task.completed_time_ns = time.time_ns()
            task.amount_paid = payment_result["Ok"]
            task.save()
            return task.id
        else:
            raise HTTPException(status_code=400, detail=f"Failed to pay executor: {payment_result['Err']}")
    else:
        raise HTTPException(status_code=400, detail=f"Task not completed satisfactorily: {response}")

def check_expired_tasks():
    now = time.time_ns()
    for task in TaskInDB.find_all_by_query(lambda t: t.status == "accepted"):
        if task.accepted_time_ns + task.completion_expiration_duration < now:
            refund_result = refund_task_payment(task)
            if "Ok" in refund_result:
                task.canceled_time_ns = now
                task.save()
            else:
                # Log the error or raise an alert
                print(f"Failed to refund payment for expired task {task.id}: {refund_result['Err']}")

from kybra import (
    Async,
    CallResult,
    ic,
    match,
    nat64,
    Principal,
    query,
    Service,
    service_update,
    StableBTreeMap,
    update,
    Variant,
)


class TokenCanister(Service):
    @service_update
    def transfer(self, to: Principal, amount: nat64) -> "TransferResult":
        ...

class TransferResult(Variant, total=False):
    Ok: nat64
    Err: "TransferError"

class TransferError(Variant, total=False):
    InsufficientBalance: nat64

token_canister = TokenCanister(Principal.from_str("r7inp-6aaaa-aaaaa-aaabq-cai"))  # Replace with the actual token canister principal

class TaskPaymentResult(Variant, total=False):
    Ok: nat64
    Err: str

def escrow_task_payment(task: TaskInDB, amount: int):
    call_result: CallResult[TransferResult] = token_canister.transfer(ic.id(), amount).call()

    def handle_transfer_result(transfer_result: TransferResult) -> TaskPaymentResult:
        return match(
            transfer_result,
            {
                "Ok": lambda amount: {"Ok": amount},
                "Err": lambda err: {"Err": str(err)},
            },
        )

    return match(
        call_result,
        {
            "Ok": handle_transfer_result,
            "Err": lambda err: {"Err": err},
        },
    )

def pay_task_executor(task: TaskInDB, amount: int):
    call_result: CallResult[TransferResult] = token_canister.transfer(task.executed_by_id, amount).call()

    def handle_transfer_result(transfer_result: TransferResult) -> TaskPaymentResult:
        return match(
            transfer_result,
            {
                "Ok": lambda amount: {"Ok": amount},
                "Err": lambda err: {"Err": str(err)},
            },
        )

    return match(
        call_result,
        {
            "Ok": handle_transfer_result,
            "Err": lambda err: {"Err": err},
        },
    )

def refund_task_payment(task: TaskInDB):
    call_result: CallResult[TransferResult] = token_canister.transfer(task.requested_by_id, task.amount_escrowed).call()

    def handle_transfer_result(transfer_result: TransferResult) -> TaskPaymentResult:
        return match(
            transfer_result,
            {
                "Ok": lambda amount: {"Ok": amount},
                "Err": lambda err: {"Err": str(err)},
            },
        )

    return match(
        call_result,
        {
            "Ok": handle_transfer_result,
            "Err": lambda err: {"Err": err},
        },
    )