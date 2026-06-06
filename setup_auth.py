"""
Run this once to authenticate with Google and create a fresh token.json.
A browser window will open — sign in with your Google account and allow access.
"""
from google_auth_oauthlib.flow import InstalledAppFlow
import os

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/spreadsheets',
]

# Must be run from project root (where credentials.json lives)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("Opening browser for Google authentication...")
print("Sign in and click Allow — then come back here.\n")

flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
creds = flow.run_local_server(port=8080)

with open('token.json', 'w') as f:
    f.write(creds.to_json())

print("\n✓ token.json written successfully.")
print("  You can now run: python main.py")
