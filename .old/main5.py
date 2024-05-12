from __future__ import annotations

import time
from typing import Annotated, Literal, Optional
from uuid import uuid4
from fastapi import Depends, FastAPI, HTTPException, UploadFile, File

import os
from fastapi import UploadFile, File
from uuid import uuid4
from fastapi.responses import FileResponse
from sqlalchemy.orm import declarative_base
from pydantic import BaseModel, Field
from fastapi_users import FastAPIUsers
# from fastapi_users.authentication import JWTAuthentication
from fastapi_users.db import SQLAlchemyBaseUserTable
from sqlalchemy import Column, String, create_engine
from sqlalchemy import Column, Integer, String, Boolean, LargeBinary, ForeignKey, func
from sqlalchemy.orm import relationship, Session
from sqlalchemy.ext.hybrid import hybrid_property
import stripe

from dotenv import load_dotenv
import os

load_dotenv()

stripe.api_key = os.getenv("STRIPE_API_KEY")
JWT_SECRET = os.getenv("JWT_SECRET")

app = FastAPI()

stripe.api_key = "your_stripe_api_key"

engine = create_engine("sqlite:///./database.db")
Base = declarative_base()


def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()


class UserTable(Base, SQLAlchemyBaseUserTable):
    id = Column(Integer, primary_key=True)  # Ensure there is a primary key column


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)

    display_name = Column(String, index=True)
    created_at = Column(Integer, default=time.time_ns)

    banned = Column(Boolean, default=False)
    blocked_user_ids = Column(String, default="")

    min_task_price = Column(Integer, default=0)
    stripe_customer_id = Column(String)

    requested_tasks = relationship(
        "Task", back_populates="requested_by", foreign_keys="Task.requested_by_id"
    )
    executed_tasks = relationship(
        "Task", back_populates="executed_by", foreign_keys="Task.executed_by_id"
    )


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, index=True)
    max_price = Column(Integer)
    min_price = Column(Integer)

    requested_by_id = Column(Integer, ForeignKey("users.id"))
    executed_by_id = Column(Integer, ForeignKey("users.id"))

    requested_by = relationship(
        "User", back_populates="requested_tasks", foreign_keys=[requested_by_id]
    )
    executed_by = relationship(
        "User", back_populates="executed_tasks", foreign_keys=[executed_by_id]
    )

    submitted_time_ns = Column(Integer, default=time.time_ns)
    accepted_time_ns = Column(Integer)
    completed_time_ns = Column(Integer)
    canceled_time_ns = Column(Integer)

    stripe_payment_intent_id = Column(String)

    messages = relationship("Message", back_populates="task")

    @hybrid_property
    def status(self) -> Literal["unassigned", "accepted", "completed", "canceled"]:
        if self.canceled_time_ns:
            return "canceled"
        elif self.completed_time_ns:
            return "completed"
        elif self.accepted_time_ns:
            return "accepted"
        else:
            return "unassigned"


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"))
    sender_id = Column(Integer, ForeignKey("users.id"))
    text = Column(String)
    image_url = Column(String)

    task = relationship("Task", back_populates="messages")


class UserRead(BaseModel):
    id: int
    display_name: str

class UserCreate(BaseModel):
    display_name: str


class UserUpdate(BaseModel):
    display_name: Optional[str]


class TaskCreate(BaseModel):
    description: str
    max_price: int
    min_price: int


class TaskUpdate(BaseModel):
    pass


class TaskQuery(BaseModel):
    status: Optional[Literal["unassigned", "accepted", "completed", "canceled"]]
    requested_by_id: Optional[int]
    executed_by_id: Optional[int]


user_db = SQLAlchemyUserDatabase(UserTable, session_factory=get_db)
jwt_authentication = JWTAuthentication(secret=JWT_SECRET, lifetime_seconds=3600)

fastapi_users = FastAPIUsers(
    user_db,
    [jwt_authentication],
    User,
    UserCreate,
    UserUpdate,
    UserRead,
)

app.include_router(
    fastapi_users.get_auth_router(jwt_authentication),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_register_router(),
    prefix="/auth",
    tags=["auth"],
)

from fastapi_users.models import BaseUserDB

CurrentUser = Annotated[User, Depends(fastapi_users.current_user(active=True))]


@app.post("/users", response_model=int)
def create_user(
    create_user_data: UserCreate,
    user_manager=Depends(fastapi_users.get_user_manager),
    db: Session = Depends(get_db),
) -> int:
    user = user_manager.create(create_user_data, safe=True)
    db.commit()
    return user.id


@app.put("/users/{user_id}", response_model=int)
def update_user(
    user_id: int,
    update_user_data: UserUpdate,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> int:
    if current_user.id != user_id:
        raise HTTPException(
            status_code=403, detail="You can only update your own profile"
        )

    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        update_data = update_user_data.dict(exclude_unset=True)
        for key, value in update_data.items():
            setattr(user, key, value)

        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tasks", response_model=int)
def create_task(
    create_task_data: TaskCreate,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> int:
    try:
        task = Task(**create_task_data.dict(), requested_by_id=current_user.id)
        db.add(task)
        db.commit()
        db.refresh(task)
        return task.id
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tasks", response_model=list[Task])
def get_available_tasks(
    query: TaskQuery = Depends(),
    current_user: CurrentUser = Depends(fastapi_users.current_user(active=True)),
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
) -> list[Task]:
    try:
        tasks_query = db.query(Task)
        if query.status:
            if query.status == "unassigned":
                tasks_query = tasks_query.filter(
                    Task.accepted_time_ns == None,
                    Task.completed_time_ns == None,
                    Task.canceled_time_ns == None,
                )
            elif query.status == "accepted":
                tasks_query = tasks_query.filter(
                    Task.accepted_time_ns != None,
                    Task.completed_time_ns == None,
                    Task.canceled_time_ns == None,
                )
            elif query.status == "completed":
                tasks_query = tasks_query.filter(Task.completed_time_ns != None)
            elif query.status == "canceled":
                tasks_query = tasks_query.filter(Task.canceled_time_ns != None)
        if query.requested_by_id:
            tasks_query = tasks_query.filter(
                Task.requested_by_id == query.requested_by_id
            )
        if query.executed_by_id:
            tasks_query = tasks_query.filter(
                Task.executed_by_id == query.executed_by_id
            )

        blocked_user_ids = (
            current_user.blocked_user_ids.split(",")
            if current_user.blocked_user_ids
            else []
        )
        tasks_query = tasks_query.filter(Task.requested_by_id.not_in(blocked_user_ids))

        tasks = tasks_query.offset(skip).limit(limit).all()
        return tasks
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/tasks/{task_id}/accept", response_model=int)
def accept_task(
    task_id: int, current_user: CurrentUser, db: Session = Depends(get_db)
) -> int:
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="The task does not exist")
        if task.status != "unassigned":
            raise HTTPException(status_code=400, detail="The task is not unassigned")
        if str(task.requested_by_id) in current_user.blocked_user_ids.split(","):
            raise HTTPException(
                status_code=403, detail="You have blocked the requester of this task"
            )
        if task.min_price < current_user.min_task_price:
            raise HTTPException(
                status_code=403, detail="The task price is below your minimum"
            )

        task.accepted_time_ns = time.time_ns()
        task.executed_by_id = current_user.id

        db.add(task)
        db.commit()
        db.refresh(task)
        return task.id
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tasks/{task_id}/messages/text", response_model=int)
def add_text_message_to_task(
    task_id: int,
    text_content: str,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> int:
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="The task does not exist")
        if task.status != "accepted":
            raise HTTPException(status_code=400, detail="The task is not in progress")
        if (
            current_user.id != task.requested_by_id
            and current_user.id != task.executed_by_id
        ):
            raise HTTPException(
                status_code=403,
                detail="You are not authorized to add messages to this task",
            )

        message = Message(task_id=task_id, sender_id=current_user.id, text=text_content)
        db.add(message)
        db.commit()
        db.refresh(message)
        return message.id
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tasks/{task_id}/messages/image", response_model=int)
def add_image_message_to_task(
    task_id: int,
    image: UploadFile = File(...),
    current_user: CurrentUser = Depends(fastapi_users.current_user(active=True)),
    db: Session = Depends(get_db),
) -> int:
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="The task does not exist")
        if task.status != "accepted":
            raise HTTPException(status_code=400, detail="The task is not in progress")
        if (
            current_user.id != task.requested_by_id
            and current_user.id != task.executed_by_id
        ):
            raise HTTPException(
                status_code=403,
                detail="You are not authorized to add messages to this task",
            )

        # Save the uploaded image to the public folder
        public_folder = "public"
        if not os.path.exists(public_folder):
            os.makedirs(public_folder)

        file_extension = os.path.splitext(image.filename)[1]
        unique_filename = f"{uuid4()}{file_extension}"
        file_path = os.path.join(public_folder, unique_filename)

        with open(file_path, "wb") as file:
            file.write(image.file.read())

        message = Message(
            task_id=task_id,
            sender_id=current_user.id,
            image_url=f"/images/{unique_filename}",
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return message.id
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/images/{filename}")
async def get_image(filename: str):
    file_path = os.path.join("public", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)


@app.get("/tasks/{task_id}/messages", response_model=list[Message])
def get_messages_for_task(
    task_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
    start: int = 0,
    end: int = None,
) -> list[Message]:
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="The task does not exist")
        if (
            current_user.id != task.requested_by_id
            and current_user.id != task.executed_by_id
        ):
            raise HTTPException(
                status_code=403,
                detail="You are not authorized to view messages for this task",
            )

        messages_query = db.query(Message).filter(Message.task_id == task_id)
        if end:
            messages_query = messages_query.slice(start, end)
        else:
            messages_query = messages_query.offset(start)

        messages = messages_query.all()
        return messages
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/tasks/{task_id}/cancel", response_model=int)
def cancel_task(
    task_id: int, current_user: CurrentUser, db: Session = Depends(get_db)
) -> int:
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="The task does not exist")
        if task.status != "accepted":
            raise HTTPException(status_code=400, detail="The task is not in progress")
        if current_user.id != task.requested_by_id:
            raise HTTPException(
                status_code=403, detail="Only the task requester can cancel the task"
            )

        task.canceled_time_ns = time.time_ns()

        db.add(task)
        db.commit()
        db.refresh(task)
        return task.id
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/tasks/{task_id}/complete", response_model=int)
def complete_task(
    task_id: int, current_user: CurrentUser, db: Session = Depends(get_db)
) -> int:
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="The task does not exist")
        if task.status != "accepted":
            raise HTTPException(status_code=400, detail="The task is not in progress")
        if current_user.id != task.executed_by_id:
            raise HTTPException(
                status_code=403, detail="Only the task executor can complete the task"
            )

        task.completed_time_ns = time.time_ns()

        db.add(task)
        db.commit()
        db.refresh(task)

        # Process payment using Stripe
        try:
            payment_intent = stripe.PaymentIntent.create(
                amount=task.max_price,
                currency="usd",
                customer=task.requested_by.stripe_customer_id,
                payment_method=task.stripe_payment_intent_id,
                off_session=True,
                confirm=True,
            )
            # Handle successful payment
            print(f"Payment successful. Payment Intent ID: {payment_intent.id}")
        except stripe.error.StripeError as e:
            # Handle Stripe error
            print(f"Stripe error: {str(e)}")
            raise HTTPException(status_code=400, detail="Payment failed")

        return task.id
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


def check_expired_tasks(db: Session):
    try:
        now = time.time_ns()
        expired_tasks = (
            db.query(Task)
            .filter(
                Task.status == "accepted",
                Task.accepted_time_ns + Task.completion_expiration_duration < now,
            )
            .all()
        )

        for task in expired_tasks:
            task.canceled_time_ns = now
            db.add(task)

        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error checking expired tasks: {str(e)}")


# Set up a scheduled job to check for expired tasks every minute
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.add_job(
    func=lambda: check_expired_tasks(next(get_db())), trigger="interval", minutes=1
)
scheduler.start()

Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
