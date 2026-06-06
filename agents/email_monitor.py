import os
import base64
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

class EmailMonitor:
    """
    Monitors and processes candidate communication via email.
    Implements Gmail resume fetching via Gmail API with OAuth2 authentication.
    """
    
    # OAuth2 scopes for Gmail readonly, Calendar, and Sheets access
    SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly',
        'https://www.googleapis.com/auth/calendar',
        'https://www.googleapis.com/auth/spreadsheets'
    ]
    
    def __init__(self, email_service=None, auth_config_id=None, connected_account_id=None, project_id=None, creds=None):
        self.email_service = email_service
        self.auth_config_id = auth_config_id
        self.connected_account_id = connected_account_id
        self.project_id = project_id
        self.creds = creds  # Use provided credentials if available
        self.service = None
        self._initialize_gmail_service()
    
    def _initialize_gmail_service(self):
        """
        Initialize Gmail API service using OAuth2 credentials.
        Uses provided credentials or initializes from credentials.json.
        """
        try:
            # If credentials were not provided, initialize them
            if not self.creds:
                # Check if token.json exists with stored credentials
                if os.path.exists('token.json'):
                    self.creds = Credentials.from_authorized_user_file('token.json', self.SCOPES)
                
                # If credentials don't exist or are invalid, run OAuth2 flow
                if not self.creds or not self.creds.valid:
                    if self.creds and self.creds.expired and self.creds.refresh_token:
                        # Refresh expired credentials
                        from google.auth.transport.requests import Request
                        self.creds.refresh(Request())
                    else:
                        # Run OAuth2 flow using credentials.json to generate new token
                        flow = InstalledAppFlow.from_client_secrets_file(
                            'credentials.json', self.SCOPES)
                        self.creds = flow.run_local_server(port=0)
                    
                    # Save credentials to token.json for future use
                    with open('token.json', 'w') as token:
                        token.write(self.creds.to_json())
            
            # Build Gmail service using valid credentials
            self.service = build('gmail', 'v1', credentials=self.creds)
            print("Gmail service initialized successfully")
        except Exception as e:
            print(f"Error initializing Gmail service: {e}")
            self.service = None
    
    def fetch_resume_emails(self, days_back: int = 7) -> List[Dict]:
        """
        Fetch emails with resume attachments from the last N days using Gmail API.
        
        Args:
            days_back: Number of days to look back for emails (default: 7)
        
        Returns:
            List of candidate info dictionaries containing email details and attachments
        """
        try:
            if not self.service:
                print("Gmail service not initialized. Cannot fetch emails.")
                return []
            
            # Step 1: Calculate date range for query
            now = datetime.now()
            start_date = now - timedelta(days=days_back)
            date_query = start_date.strftime('%Y/%m/%d')  # Format: YYYY/MM/DD for Gmail API
            
            # Step 2: Build Gmail search query — subject OR body keywords
            query = (
                f'has:attachment after:{date_query} in:inbox '
                f'(subject:resume OR subject:cv OR subject:application OR subject:"job application" '
                f'OR subject:"applying for" OR subject:candidate OR subject:"cover letter" '
                f'OR body:resume OR body:"cover letter" OR body:"years of experience" '
                f'OR body:"i am applying" OR body:"please find attached")'
            )
            
            print(f"Searching for emails with attachments since {date_query}...")
            
            # Step 3: Execute search query
            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=50  # Limit results for performance
            ).execute()
            
            messages = results.get('messages', [])
            print(f"Found {len(messages)} messages with attachments")
            
            resume_emails = []
            
            # Step 4: Fetch and process each message
            for msg in messages:
                try:
                    # Step 5: Get full message details
                    message = self.service.users().messages().get(
                        userId='me',
                        id=msg['id'],
                        format='full'
                    ).execute()
                    
                    # Extract headers (From, Subject, Date)
                    headers = message['payload'].get('headers', [])
                    subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
                    sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown Sender')
                    date = next((h['value'] for h in headers if h['name'].lower() == 'date'), '')
                    
                    # Parse sender name and email
                    email_match = re.search(r'<(.+?)>', sender)
                    if email_match:
                        sender_email = email_match.group(1)
                        sender_name = sender.split('<')[0].strip().strip('"')
                    else:
                        sender_email = sender
                        sender_name = sender
                    
                    # Extract attachments
                    attachments = []
                    parts = message['payload'].get('parts', [])
                    
                    def extract_attachments(parts_list):
                        for part in parts_list:
                            if part.get('filename'):
                                filename = part['filename']
                                if filename.lower().endswith(('.pdf', '.doc', '.docx')):
                                    attachment_id = part['body'].get('attachmentId')
                                    pdf_bytes = None
                                    if attachment_id:
                                        try:
                                            att = self.service.users().messages().attachments().get(
                                                userId='me',
                                                messageId=msg['id'],
                                                id=attachment_id
                                            ).execute()
                                            pdf_bytes = base64.urlsafe_b64decode(att['data'])
                                        except Exception as e:
                                            print(f"Error downloading attachment {filename}: {e}")
                                    attachments.append({
                                        'filename': filename,
                                        'mimeType': part.get('mimeType', ''),
                                        'size': part.get('body', {}).get('size', 0),
                                        'bytes': pdf_bytes
                                    })
                            if part.get('parts'):
                                extract_attachments(part['parts'])

                    extract_attachments(parts)
                    
                    # Step 6: Build candidate info dictionary
                    if attachments:  # Only include if attachments found
                        candidate_info = {
                            'message_id': msg['id'],
                            'sender_name': sender_name,
                            'sender_email': sender_email,
                            'subject': subject,
                            'date': date,
                            'attachments': attachments,
                            'thread_id': message.get('threadId', '')
                        }
                        resume_emails.append(candidate_info)
                        
                except Exception as e:
                    print(f"Error processing message {msg['id']}: {e}")
                    continue
            
            print(f"Found {len(resume_emails)} resume emails from the last {days_back} days")
            return resume_emails
            
        except HttpError as error:
            print(f"An error occurred while fetching resume emails: {error}")
            return []
        except Exception as e:
            print(f"Unexpected error in fetch_resume_emails: {e}")
            return []
