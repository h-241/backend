import time
from typing import Annotated, AsyncGenerator, ClassVar, Literal

from fastapi import Depends
from fastapi_users.db import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship


DATABASE_URL = "sqlite+aiosqlite:///./test.db"


class Base(DeclarativeBase):
    id = Column(Integer, primary_key=True, index=True)
    ID: ClassVar[int] = int

class User(SQLAlchemyBaseUserTableUUID, Base):
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

engine = create_async_engine(DATABASE_URL)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def create_db_and_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)

import uuid

from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    pass


class UserCreate(schemas.BaseUserCreate):
    pass


class UserUpdate(schemas.BaseUserUpdate):
    pass

import uuid
from typing import Optional

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase

SECRET = "SECRET"


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        print(f"User {user.id} has registered.")

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        print(f"User {user.id} has forgot their password. Reset token: {token}")

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        print(f"Verification requested for user {user.id}. Verification token: {token}")


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=SECRET, lifetime_seconds=3600)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Not needed if you setup a migration system like Alembic
    await create_db_and_tables()
    yield


app = FastAPI(lifespan=lifespan)

app.include_router(
    fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"]
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_verify_router(UserRead),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)


@app.get("/authenticated-route")
async def authenticated_route(user: User = Depends(current_active_user)):
    return {"message": f"Hello {user.email}!"}

CurrentUser = Annotated[User, Depends(current_active_user)]



############# START HERE #############


import stripe

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
