"""
RUN THIS ONCE — in Google Colab, not on your phone directly.
It walks you through Google login and prints a refresh token.
Copy that refresh token into your GitHub Secrets as YT_REFRESH_TOKEN.

Before running, replace CLIENT_ID and CLIENT_SECRET below with the values
from your Google Cloud OAuth Client (see SETUP_GUIDE.md, step 3).
"""

from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_ID = "PASTE_YOUR_CLIENT_ID_HERE"
CLIENT_SECRET = "PASTE_YOUR_CLIENT_SECRET_HERE"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

client_config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
    }
}

flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
# Colab-friendly: prints a URL, you open it, log in, paste the code back
creds = flow.run_console()

print("\n\n==== COPY THIS REFRESH TOKEN INTO YOUR GITHUB SECRETS ====")
print(creds.refresh_token)
print("============================================================")
