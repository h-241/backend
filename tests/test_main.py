from fastapi.testclient import TestClient
from app.__main__ import app, get_db, User, Task, Message

client = TestClient(app)

def test_create_user():
    response = client.post(
        "/users",
        json={"email": "test@example.com", "password": "password", "display_name": "Test User"},
    )
    assert response.status_code == 200
    assert response.json() == {"id": 1}

def test_create_task():
    # Create a user first
    client.post(
        "/users",
        json={"email": "test@example.com", "password": "password", "display_name": "Test User"},
    )
    # Log in the user to get an access token
    response = client.post(
        "/auth/jwt/login",
        data={"username": "test@example.com", "password": "password"},
    )
    access_token = response.json()["access_token"]
    # Create a task
    response = client.post(
        "/tasks",
        json={"description": "Test Task", "max_price": 100, "min_price": 50},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200
    assert response.json() == {"id": 1}

def test_get_available_tasks():
    # Create a user and a task first
    client.post(
        "/users",
        json={"email": "test@example.com", "password": "password", "display_name": "Test User"},
    )
    response = client.post(
        "/auth/jwt/login",
        data={"username": "test@example.com", "password": "password"},
    )
    access_token = response.json()["access_token"]
    client.post(
        "/tasks",
        json={"description": "Test Task", "max_price": 100, "min_price": 50},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    # Get available tasks
    response = client.get("/tasks")
    assert response.status_code == 200
    assert len(response.json()) == 1