from __future__ import annotations
import time
from typing import Annotated, Literal, Optional
from uuid import uuid4
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Field, Relationship, Session, SQLModel, create_engine, select

import stripe

app = FastAPI()

stripe.api_key = "your_stripe_api_key"

engine = create_engine("sqlite:///./database.db")
SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session

class UserBase(SQLModel):
    display_name: str
    identity: str

class User(UserBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: int = Field(default_factory=time.time_ns)
    
    banned: bool = False
    blocked_user_ids: list[int] = Field(default_factory=list)

    requested_tasks: list["Task"] = Relationship(back_populates="requested_by")
    executed_tasks: list["Task"] = Relationship(back_populates="executed_by")
    
    min_task_execute_duration: int = 0
    min_task_execute_price: int = 0

class TaskBase(SQLModel):
    description: str
    max_price: int
    min_price: int

class Task(TaskBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    requested_by_id: Optional[int] = Field(default=None, foreign_key="user.id")
    executed_by_id: Optional[int] = Field(default=None, foreign_key="user.id")
    
    requested_by: Optional[User] = Relationship(back_populates="requested_tasks")
    executed_by: Optional[User] = Relationship(back_populates="executed_tasks")
    
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
    
    messages: list["Message"] = Relationship(back_populates="task")

class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: Optional[int] = Field(default=None, foreign_key="task.id")
    sender_id: Optional[int] = Field(default=None, foreign_key="user.id")
    text: Optional[str] = None
    image: Optional[bytes] = None

    task: Optional[Task] = Relationship(back_populates="messages")

class UserCreate(UserBase):
    pass

class UserUpdate(UserBase):
    pass

class TaskCreate(TaskBase):
    pass

class TaskUpdate(TaskBase):
    pass

class TaskQuery(BaseModel):
    status: Optional[Literal["unassigned", "accepted", "completed", "canceled"]]
    requested_by_id: Optional[int]
    executed_by_id: Optional[int]

CurrentUser = Annotated[User, Depends(get_current_user)]

def get_current_user(identity: str, session: Session = Depends(get_session)) -> User:
    statement = select(User).where(User.identity == identity)
    user = session.exec(statement).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@app.post("/users", response_model=int)
def create_user(create_user_data: UserCreate, session: Session = Depends(get_session)) -> int:
    user = User(**create_user_data.dict())
    session.add(user)
    session.commit()
    session.refresh(user)
    return user.id

@app.put("/users/{user_id}", response_model=int)
def update_user(user_id: int, update_user_data: UserUpdate, current_user: CurrentUser, session: Session = Depends(get_session)) -> int:
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="You can only update your own profile")
    
    statement = select(User).where(User.id == user_id)
    user = session.exec(statement).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    for key, value in update_user_data.dict(exclude_unset=True).items():
        setattr(user, key, value)
    
    session.add(user)
    session.commit()
    session.refresh(user)
    return user.id

@app.post("/tasks", response_model=int)
def create_task(create_task_data: TaskCreate, current_user: CurrentUser, session: Session = Depends(get_session)) -> int:
    task = Task(**create_task_data.dict(), requested_by_id=current_user.id)
    session.add(task)
    session.commit()
    session.refresh(task)
    return task.id

@app.get("/tasks", response_model=list[Task])
def get_available_tasks(query: TaskQuery = Depends(), current_user: CurrentUser = Depends(get_current_user), session: Session = Depends(get_session), skip: int = 0, limit: int = 100) -> list[Task]:
    statement = select(Task)
    if query.status:
        statement = statement.where(Task.status == query.status)
    if query.requested_by_id:
        statement = statement.where(Task.requested_by_id == query.requested_by_id)
    if query.executed_by_id:
        statement = statement.where(Task.executed_by_id == query.executed_by_id)
    
    statement = statement.where(Task.requested_by_id.not_in(current_user.blocked_user_ids))
    statement = statement.offset(skip).limit(limit)
    
    tasks = session.exec(statement).all()
    return tasks

@app.put("/tasks/{task_id}/accept", response_model=int)
def accept_task(task_id: int, current_user: CurrentUser, session: Session = Depends(get_session)) -> int:
    statement = select(Task).where(Task.id == task_id)
    task = session.exec(statement).first()
    if not task:
        raise HTTPException(status_code=404, detail="The task does not exist")
    if task.status != "unassigned":
        raise HTTPException(status_code=400, detail="The task is not unassigned")
    if task.requested_by_id in current_user.blocked_user_ids:
        raise HTTPException(status_code=403, detail="You have blocked the requester of this task")
    if task.min_price < current_user.min_task_execute_price:
        raise HTTPException(status_code=403, detail="The task price is below your minimum")
    
    task.accepted_time_ns = time.time_ns()
    task.executed_by_id = current_user.id
    
    session.add(task)
    session.commit()
    session.refresh(task)
    return task.id

@app.post("/tasks/{task_id}/messages/text", response_model=int)
def add_text_message_to_task(task_id: int, text_content: str, current_user: CurrentUser, session: Session = Depends(get_session)) -> int:
    statement = select(Task).where(Task.id == task_id)
    task = session.exec(statement).first()
    if not task:
        raise HTTPException(status_code=404, detail="The task does not exist")
    if task.status != "accepted":
        raise HTTPException(status_code=400, detail="The task is not in progress")
    if current_user.id != task.requested_by_id and current_user.id != task.executed_by_id:
        raise HTTPException(status_code=403, detail="You are not authorized to add messages to this task")
    
    message = Message(task_id=task_id, sender_id=current_user.id, text=text_content)
    session.add(message)
    session.commit()
    session.refresh(message)
    return message.id

@app.post("/tasks/{task_id}/messages/image", response_model=int)
def add_image_message_to_task(task_id: int, image_content: bytes, current_user: CurrentUser, session: Session = Depends(get_session)) -> int:
    statement = select(Task).where(Task.id == task_id)
    task = session.exec(statement).first()
    if not task:
        raise HTTPException(status_code=404, detail="The task does not exist")
    if task.status != "accepted":
        raise HTTPException(status_code=400, detail="The task is not in progress")
    if current_user.id != task.requested_by_id and current_user.id != task.executed_by_id:
        raise HTTPException(status_code=403, detail="You are not authorized to add messages to this task")
    if len(image_content) > 1024*1024:  # Limit images to 1MB
        raise HTTPException(status_code=400, detail="Image size exceeds the 1MB limit")
    
    message = Message(task_id=task_id, sender_id=current_user.id, image=image_content)
    session.add(message)
    session.commit()
    session.refresh(message)
    return message.id

@app.get("/tasks/{task_id}/messages", response_model=list[Message])
def get_messages_for_task(task_id: int, current_user: CurrentUser, session: Session = Depends(get_session)) -> list[Message]:
    statement = select(Task).where(Task.id == task_id)
    task = session.exec(statement).first()
    if not task:
        raise HTTPException(status_code=404, detail="The task does not exist")
    if current_user.id != task.requested_by_id and current_user.id != task.executed_by_id:
        raise HTTPException(status_code=403, detail="You are not authorized to view messages for this task")
    
    statement = select(Message).where(Message.task_id == task_id)
    messages = session.exec(statement).all()
    return messages

@app.put("/tasks/{task_id}/cancel", response_model=int)
def cancel_task(task_id: int, current_user: CurrentUser, session: Session = Depends(get_session)) -> int:
    statement = select(Task).where(Task.id == task_id)
    task = session.exec(statement).first()
    if not task:
        raise HTTPException(status_code=404, detail="The task does not exist")
    if task.status != "accepted":
        raise HTTPException(status_code=400, detail="The task is not in progress")
    if current_user.id != task.requested_by_id:
        raise HTTPException(status_code=403, detail="Only the task requester can cancel the task")
    
    task.canceled_time_ns = time.time_ns()
    
    session.add(task)
    session.commit()
    session.refresh(task)
    return task.id

@app.put("/tasks/{task_id}/complete", response_model=int)
def complete_task(task_id: int, current_user: CurrentUser, session: Session = Depends(get_session)) -> int:
    statement = select(Task).where(Task.id == task_id)
    task = session.exec(statement).first()
    if not task:
        raise HTTPException(status_code=404, detail="The task does not exist")
    if task.status != "accepted":
        raise HTTPException(status_code=400, detail="The task is not in progress")
    if current_user.id != task.executed_by_id:
        raise HTTPException(status_code=403, detail="Only the task executor can complete the task")
    
    task.completed_time_ns = time.time_ns()
    
    session.add(task)
    session.commit()
    session.refresh(task)
    
    # Process payment using Stripe
    try:
        charge = stripe.Charge.create(
            amount=task.max_price,
            currency="usd",
            description=f"Payment for task {task.id}",
            source="tok_visa",  # Replace with the actual token obtained from Stripe.js
        )
        # Handle successful payment
        print(f"Payment successful. Charge ID: {charge.id}")
    except stripe.error.StripeError as e:
        # Handle Stripe error
        print(f"Stripe error: {str(e)}")
        raise HTTPException(status_code=400, detail="Payment failed")
    
    return task.id

def check_expired_tasks(session: Session):
    now = time.time_ns()
    statement = select(Task).where(Task.status == "accepted")
    tasks = session.exec(statement).all()
    
    for task in tasks:
        if task.accepted_time_ns + task.completion_expiration_duration < now:
            task.canceled_time_ns = now
            session.add(task)
    
    session.commit()