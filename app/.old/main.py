from __future__ import annotations
import time
from typing import Annotated, Any, Callable, Literal, Optional, Self
from uuid import uuid4
from fastapi import Depends

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
        return cls._ON_CHAIN_STORAGE.get(cls, {}).get[id]
    
    @classmethod
    def find_all(cls) -> list[Self]:
        return list(cls._ON_CHAIN_STORAGE.get(cls, {}).values())
    
    @classmethod
    def find_all_by_ids(cls, ids: list[UUID4]) -> list[Self]:
        items = [cls.find_by_id(id) for id in ids if cls.find_by_id(id)]
        items = list(filter(lambda item: item is not None, items))
        return items
    
    @classmethod
    def find_all_by_query(cls, query: Callable[[Any], bool]) -> list[Self]:
        return [obj for obj in cls.find_all() if query(obj)]
    
    @classmethod
    def find_or_none(cls, id: UUID4) -> Optional[Self]:
        try:
            return cls.find_by_id(id)
        except ValueError:
            return None
    
    @classmethod
    def delete_by_id(cls, id: UUID4):
        cls._ON_CHAIN_STORAGE.get(cls, {}).pop(id)
    
    @classmethod
    def delete_all(cls):
        cls._ON_CHAIN_STORAGE.get(cls, {}).clear()
    
    @classmethod
    def delete_all_by_ids(cls, ids: list[UUID4]):
        for id in ids:
            cls.delete_by_id(id)
    
    def delete(self):
        self._ON_CHAIN_STORAGE.get(type(self), {}).pop(self.id)

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
    blocked_user_ids: list[UserInDB.ID]

    requested_task_ids: list[TaskInDB.ID]
    executed_task_ids: list[TaskInDB.ID]
    
    min_task_execute_duration: int
    min_task_execute_price: int

class TaskBase(ModelInDB):
    description: str
    max_price: int
    min_price: int

class TaskCreate(TaskBase):
    pass

class TaskUpdate(TaskBase):
    pass

class TaskInDB(TaskBase):
    requested_by_id: UserInDB.ID
    executed_by_id: UserInDB.ID
    
    amount_escrowed: int
    amount_paid: int
    
    match_expiration_duration: int
    completion_expiration_duration: int
    
    submitted_time_ns: int
    accepted_time_ns: int
    completed_time_ns: int
    canceled_time_ns: int
    
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
    
    messages: list[Message]

class Message(BaseModel):
    task_id: TaskInDB.ID
    sender_id: UserInDB.ID
    text: str
    image: bytearray


CurrentUser = Annotated[UserInDB, Depends(get_current_user)] # i don't know how to get the user from the IC VM

def create_user(create_user_data: UserCreate, current_identity: Annotated[Identity, Depends(get_current_identity)]) -> UUID4:
    # authorize that the current_identity is the same as the public_key
    if current_identity.public_key != create_user_data.public_key:
        raise ValueError("The public_key must match the current_identity")
    
    user = UserInDB.create(create_user_data)
    user.save()
    return user.id

def create_task(create_task_data: TaskCreate, current_user: CurrentUser) -> UUID4:
    # authorize that the current_identity is the same as the public_key
    if current_user.id != create_task_data.requested_by_id:
        raise ValueError("The current_user does not have permission to create a task for the user with the given id")
    
    task = TaskInDB.create(create_task_data)
    task.save()
    return task.id

def get_available_tasks(current_user: CurrentUser) -> list[TaskInDB]:
    return TaskInDB.find_all_by_query(lambda task: task.status == "unassigned")

def accept_task(task_id: UUID4, current_user: CurrentUser) -> UUID4:
    task = TaskInDB.find_or_none(task_id)
    if task is None:
        raise ValueError("The task does not exist")
    if task.status != "unassigned":
        raise ValueError("The task is not unassigned")
    task.accepted_time_ns = time.time_ns()
    task.executed_by_id = current_user.id
    task.save()
    return task.id

def add_text_message_to_task(task_id: UUID4, text_content: str, current_user: CurrentUser):
    task = TaskInDB.find_or_none(task_id)
    if task is None:
        raise ValueError("The task does not exist")
    if task.status != "accepted":
        raise ValueError("The task is not accepted")
    message = Message(task_id=task_id, sender_id=current_user.id, text=text_content)
    task.messages.append(message)
    task.save()
    return message.id

def add_image_message_to_task(task_id: UUID4, image_content: bytearray, current_user: CurrentUser):
    task = TaskInDB.find_or_none(task_id)
    if task is None:
        raise ValueError("The task does not exist")
    if task.status != "accepted":
        raise ValueError("The task is not accepted")
    message = Message(task_id=task_id, sender_id=current_user.id, image=image_content)
    task.messages.append(message)
    task.save()
    return message.id

def get_messages_for_task(task_id: UUID4, current_user: CurrentUser) -> list[str]:
    task = TaskInDB.find_or_none(task_id)
    if task is None:
        raise ValueError("The task does not exist")
    if task.status != "accepted":
        raise ValueError("The task is not accepted")
    return task.messages

def cancel_task(task_id: UUID4, current_user: CurrentUser):
    task = TaskInDB.find_or_none(task_id)
    if task is None:
        raise ValueError("The task does not exist")
    if task.status != "accepted":
        raise ValueError("The task is not accepted")
    task.canceled_time_ns = time.time_ns()
    task.save()
    return task.id

def complete_task(task_id: UUID4, current_user: CurrentUser):
    task = TaskInDB.find_or_none(task_id)
    if task is None:
        raise ValueError("The task does not exist")
    if task.status != "accepted":
        raise ValueError("The task is not accepted")
    task.completed_time_ns = time.time_ns()
    task.save()
    return task.id
