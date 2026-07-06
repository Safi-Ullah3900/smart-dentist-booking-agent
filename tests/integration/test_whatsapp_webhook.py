import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.fast_api_app import app
from app.config import config

client = TestClient(app)

def test_whatsapp_webhook_verification_success():
    """Test that webhook verification returns challenge when verify token matches."""
    verify_token = "test_handshake_token"
    config.whatsapp_verify_token = verify_token
    
    response = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": verify_token,
            "hub.challenge": "123456789"
        }
    )
    
    assert response.status_code == 200
    assert response.text == "123456789"

def test_whatsapp_webhook_verification_failure():
    """Test that webhook verification returns 403 on invalid verify token."""
    config.whatsapp_verify_token = "correct_token"
    
    response = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "123456789"
        }
    )
    
    assert response.status_code == 403

@patch("urllib.request.urlopen")
def test_whatsapp_webhook_post_ignored_status(mock_urlopen):
    """Test that webhook status notifications (delivered, read) are ignored and not processed."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "12345",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "statuses": [
                                {
                                    "id": "status_id",
                                    "status": "delivered",
                                    "timestamp": "1665096238",
                                    "recipient_id": "923001234567"
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }
    
    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["reason"] == "Status update"
    mock_urlopen.assert_not_called()

@patch("urllib.request.urlopen")
def test_whatsapp_webhook_post_ignored_non_text(mock_urlopen):
    """Test that non-text messages (e.g. image, video) are ignored."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "12345",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": "923001234567",
                                    "id": "wamid.123",
                                    "timestamp": "1665096238",
                                    "type": "image",
                                    "image": {
                                        "caption": "tooth.png"
                                    }
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }
    
    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["reason"] == "Non-text message"
    mock_urlopen.assert_not_called()

@pytest.mark.asyncio
@patch("app.fast_api_app.send_whatsapp_message")
async def test_whatsapp_webhook_post_success(mock_send_message):
    """Test that a valid incoming text message executes the runner and succeeds."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "12345",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": "923001234567",
                                    "id": "wamid.abc",
                                    "timestamp": "1665096238",
                                    "text": {
                                        "body": "Hi, what services do you offer?"
                                    },
                                    "type": "text"
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }
    
    mock_event = MagicMock()
    mock_event.content.parts = [MagicMock(text="We offer general dental checkups and teeth cleaning.")]
    
    async def mock_run_async(*args, **kwargs):
        yield mock_event
        
    mock_runner = MagicMock()
    mock_runner.run_async = mock_run_async
    
    app.state.runner = mock_runner
    
    config.whatsapp_api_token = "valid_api_token"
    config.whatsapp_phone_number_id = "valid_phone_number_id"
    
    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "We offer general dental checkups" in data["response"]
    
    mock_send_message.assert_called_once_with("923001234567", data["response"])
