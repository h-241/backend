from fastapi.testclient import TestClient
from main import fastapi

client = TestClient(fastapi)

def test_create_user():
    response = client.post("/auth/register", json={
        "email": "test@example.com",
        "password": "password",
        "display_name": "Test User"
    })
    assert response.status_code == 201
    assert "id" in response.json()

def test_create_task():
    # Authenticate and get access token
    auth_response = client.post("/auth/jwt/login", data={
        "username": "test@example.com",
        "password": "password"
    })
    access_token = auth_response.json()["access_token"]

    # Create a task
    response = client.post(
        "/tasks",
        json={
            "description": "Test Task",
            "max_price": 100,
            "min_price": 50
        },
        headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    assert "id" in response.json()

# Add more test cases for other API endpoints