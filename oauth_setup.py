#!/usr/bin/env python3
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import json
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar",
]

def setup_oauth():
    """Run this locally to generate token.json"""
    creds = None
    token_file = Path("token.json")
    credentials_file = Path("credentials.json")
    
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            # This will open a browser window
            creds = flow.run_local_server(port=0)
        
        token_file.write_text(creds.to_json(), encoding="utf-8")
        print("✅ Token saved to token.json")
    
    # Print token as base64 for Render secrets
    import base64
    with open("token.json", "r") as f:
        token_content = f.read()
    encoded = base64.b64encode(token_content.encode()).decode()
    print("\n📋 Add this to Render secrets as TOKEN_JSON:")
    print(encoded)

if __name__ == "__main__":
    setup_oauth()