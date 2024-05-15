import os
import time
import uuid
from contextlib import contextmanager
from typing import Annotated, Generator, Literal, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, schemas
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlmodel import Field, Relationship, SQLModel, create_engine, select
import stripe

DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(DATABASE_URL)


def get_session():
    with Session(engine) as session:
        yield session


@contextmanager
def get_session_context() -> Generator[Session, None, None]:
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except:
            session.rollback()
            raise


class UserBase(BaseModel):
    display_name: str = Field(index=True)


class User(UserBase, SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    created_at: int = Field(default=time.time_ns)
    hashed_password: str = Field(nullable=False)

    banned: bool = Field(default=False)
    blocked_user_ids: str = Field(default="")
    stripe_customer_id: str = Field(default=None)

    min_task_price: int = Field(default=0)
    requested_tasks: list["Task"] = Relationship(back_populates="requested_by")
    executed_tasks: list["Task"] = Relationship(back_populates="executed_by")


class UserRead(UserBase, schemas.BaseUser[uuid.UUID]):
    pass


class UserCreate(UserBase, schemas.BaseUserCreate):
    pass


class UserUpdate(UserBase, schemas.BaseUserUpdate):
    pass


class TaskBase(SQLModel):
    pass


class Task(TaskBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    description: str = Field(index=True)
    max_price: int
    min_price: int
    submitted_time_ns: int = Field(default=time.time_ns)
    accepted_time_ns: Optional[int] = Field(default=None)
    completed_time_ns: Optional[int] = Field(default=None)
    canceled_time_ns: Optional[int] = Field(default=None)
    stripe_payment_intent_id: Optional[str] = Field(default=None)

    requested_by_id: Optional[int] = Field(default=None, foreign_key="user.id")
    executed_by_id: Optional[int] = Field(default=None, foreign_key="user.id")
    requested_by: Optional[User] = Relationship(back_populates="requested_tasks")
    executed_by: Optional[User] = Relationship(back_populates="executed_tasks")

    messages: list["Message"] = Relationship(back_populates="task")

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


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: Optional[int] = Field(default=None, foreign_key="task.id")
    sender_id: Optional[int] = Field(default=None, foreign_key="user.id")
    text: Optional[str] = Field(default=None)
    image_url: Optional[str] = Field(default=None)
    task: Optional[Task] = Relationship(back_populates="messages")


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_user_db():
    with Session(engine) as session:
        yield SQLAlchemyUserDatabase(session, User)


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


def get_user_manager(user_db=Depends(get_user_db)):
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
CurrentUser = User # Annotated[User, Depends(current_active_user)]


fastapi = FastAPI()

fastapi.include_router(
    fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"]
)
fastapi.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
fastapi.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/auth",
    tags=["auth"],
)
fastapi.include_router(
    fastapi_users.get_verify_router(UserRead),
    prefix="/auth",
    tags=["auth"],
)
fastapi.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)


@fastapi.get("/authenticated-route")
async def authenticated_route(user: User = Depends(current_active_user)):
    return {"message": f"Hello {user.email}!"}


@fastapi.post("/tasks", response_model=int)
def create_task(
    create_task_data: TaskCreate,
    current_user: CurrentUser,
    session: Session = Depends(get_session),
) -> int:
    try:
        with get_session_context() as session:
            task = Task(**create_task_data.dict(), requested_by_id=current_user.id)
            session.add(task)
            session.commit()
            session.refresh(task)
            return task.id
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@fastapi.get("/tasks", response_model=list[Task])
def get_available_tasks(
    query: TaskQuery = Depends(),
    current_user: CurrentUser = Depends(fastapi_users.current_user(active=True)),
    session: Session = Depends(get_session),
    skip: int = 0,
    limit: int = 100,
) -> list[Task]:
    try:
        with get_session_context() as session:
            tasks_query = select(Task)
            if query.status:
                if query.status == "unassigned":
                    tasks_query = tasks_query.where(
                        Task.accepted_time_ns == None,
                        Task.completed_time_ns == None,
                        Task.canceled_time_ns == None,
                    )
                elif query.status == "accepted":
                    tasks_query = tasks_query.where(
                        Task.accepted_time_ns != None,
                        Task.completed_time_ns == None,
                        Task.canceled_time_ns == None,
                    )
                elif query.status == "completed":
                    tasks_query = tasks_query.where(Task.completed_time_ns != None)
                elif query.status == "canceled":
                    tasks_query = tasks_query.where(Task.canceled_time_ns != None)
            if query.requested_by_id:
                tasks_query = tasks_query.where(
                    Task.requested_by_id == query.requested_by_id
                )
            if query.executed_by_id:
                tasks_query = tasks_query.where(
                    Task.executed_by_id == query.executed_by_id
                )

            blocked_user_ids = (
                current_user.blocked_user_ids.split(",")
                if current_user.blocked_user_ids
                else []
            )
            tasks_query = tasks_query.where(
                Task.requested_by_id.not_in(blocked_user_ids)
            )

            tasks = session.exec(tasks_query.offset(skip).limit(limit)).all()
            return tasks
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@fastapi.put("/tasks/{task_id}/accept", response_model=int)
def accept_task(
    task_id: int,
    current_user: CurrentUser,
    session: Session = Depends(get_session),
) -> int:
    try:
        with get_session_context() as session:
            task = session.get(Task, task_id)
            if not task:
                raise HTTPException(status_code=404, detail="The task does not exist")
            if task.status != "unassigned":
                raise HTTPException(
                    status_code=400, detail="The task is not unassigned"
                )
            if str(task.requested_by_id) in current_user.blocked_user_ids.split(","):
                raise HTTPException(
                    status_code=403,
                    detail="You have blocked the requester of this task",
                )
            if task.min_price < current_user.min_task_price:
                raise HTTPException(
                    status_code=403, detail="The task price is below your minimum"
                )

            task.accepted_time_ns = time.time_ns()
            task.executed_by_id = current_user.id

            session.add(task)
            session.commit()
            session.refresh(task)
            return task.id
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@fastapi.post("/tasks/{task_id}/messages/text", response_model=int)
def add_text_message_to_task(
    task_id: int,
    text_content: str,
    current_user: CurrentUser,
    session: Session = Depends(get_session),
) -> int:
    try:
        with get_session_context() as session:
            task = session.get(Task, task_id)
            if not task:
                raise HTTPException(status_code=404, detail="The task does not exist")
            if task.status != "accepted":
                raise HTTPException(
                    status_code=400, detail="The task is not in progress"
                )
            if (
                current_user.id != task.requested_by_id
                and current_user.id != task.executed_by_id
            ):
                raise HTTPException(
                    status_code=403,
                    detail="You are not authorized to add messages to this task",
                )

            message = Message(
                task_id=task_id, sender_id=current_user.id, text=text_content
            )
            session.add(message)
            session.commit()
            session.refresh(message)
            return message.id
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@fastapi.post("/tasks/{task_id}/messages/image", response_model=int)
def add_image_message_to_task(
    task_id: int,
    image: UploadFile = File(...),
    current_user: CurrentUser = Depends(fastapi_users.current_user(active=True)),
    session: Session = Depends(get_session),
) -> int:
    try:
        with get_session_context() as session:
            task = session.get(Task, task_id)
            if not task:
                raise HTTPException(status_code=404, detail="The task does not exist")
            if task.status != "accepted":
                raise HTTPException(
                    status_code=400, detail="The task is not in progress"
                )
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
            unique_filename = f"{uuid.uuid4()}{file_extension}"
            file_path = os.path.join(public_folder, unique_filename)

            with open(file_path, "wb") as file:
                file.write(image.file.read())

            message = Message(
                task_id=task_id,
                sender_id=current_user.id,
                image_url=f"/images/{unique_filename}",
            )
            session.add(message)
            session.commit()
            session.refresh(message)
            return message.id
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@fastapi.get("/images/{filename}")
async def get_image(filename: str):
    file_path = os.path.join("public", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)


@fastapi.get("/tasks/{task_id}/messages", response_model=list[Message])
def get_messages_for_task(
    task_id: int,
    current_user: CurrentUser,
    session: Session = Depends(get_session),
    start: int = 0,
    end: int = None,
) -> list[Message]:
    try:
        with get_session_context() as session:
            task = session.get(Task, task_id)
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

            messages_query = select(Message).where(Message.task_id == task_id)
            if end:
                messages_query = messages_query = messages_query.offset(start).limit(
                    end - start
                )
            else:
                messages_query = messages_query.offset(start)

            messages = session.exec(messages_query).all()
            return messages
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@fastapi.put("/tasks/{task_id}/cancel", response_model=int)
def cancel_task(
    task_id: int,
    current_user: CurrentUser,
    session: Session = Depends(get_session),
) -> int:
    try:
        with get_session_context() as session:
            task = session.get(Task, task_id)
            if not task:
                raise HTTPException(status_code=404, detail="The task does not exist")
            if task.status != "accepted":
                raise HTTPException(
                    status_code=400, detail="The task is not in progress"
                )
            if current_user.id != task.requested_by_id:
                raise HTTPException(
                    status_code=403,
                    detail="Only the task requester can cancel the task",
                )

            task.canceled_time_ns = time.time_ns()

            session.add(task)
            session.commit()
            session.refresh(task)
            return task.id
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@fastapi.put("/tasks/{task_id}/complete", response_model=int)
def complete_task(
    task_id: int,
    current_user: CurrentUser,
    session: Session = Depends(get_session),
) -> int:
    try:
        with get_session_context() as session:
            task = session.get(Task, task_id)
            if not task:
                raise HTTPException(status_code=404, detail="The task does not exist")
            if task.status != "accepted":
                raise HTTPException(
                    status_code=400, detail="The task is not in progress"
                )
            if current_user.id != task.executed_by_id:
                raise HTTPException(
                    status_code=403,
                    detail="Only the task executor can complete the task",
                )

            task.completed_time_ns = time.time_ns()

            session.add(task)
            session.commit()
            session.refresh(task)

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
        raise HTTPException(status_code=500, detail=str(e))


def check_expired_tasks(session: Session):
    try:
        now = time.time_ns()
        expired_tasks = session.exec(
            select(Task).where(
                Task.status == "accepted",
                Task.accepted_time_ns + Task.completion_expiration_duration < now,
            )
        ).all()

        for task in expired_tasks:
            task.canceled_time_ns = now
            session.add(task)

        session.commit()
    except Exception as e:
        session.rollback()
        print(f"Error checking expired tasks: {str(e)}")


# Set up a scheduled job to check for expired tasks every minute
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.add_job(
    func=lambda: check_expired_tasks(next(get_session())),
    trigger="interval",
    minutes=1,
)
scheduler.start()

create_db_and_tables()

# call uvicorn app:app --reload
