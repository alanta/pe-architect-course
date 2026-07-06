#!/workspaces/pe-architect-course/workshop/teams-management/cli/venv/bin/python3
"""
Teams CLI - A simple command-line interface for the Teams API
"""

import argparse
import json
import stat
import sys
import os
import time
import webbrowser
import requests
from pathlib import Path
from typing import Optional

API_BASE_URL = os.environ.get("TEAMS_API_URL", "http://teams-api.localhost:8080")
KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://platform-auth.localhost:8080")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "teams")
KEYCLOAK_CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "teams-cli")
CREDENTIALS_PATH = Path.home() / ".teams-cli" / "credentials.json"


def _realm_url(path: str) -> str:
    return f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}{path}"


def _save_credentials(token_response: dict):
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "access_token": token_response["access_token"],
        "refresh_token": token_response.get("refresh_token"),
        "expires_at": time.time() + token_response.get("expires_in", 3600),
    }
    with open(CREDENTIALS_PATH, "w") as f:
        json.dump(data, f)
    os.chmod(CREDENTIALS_PATH, stat.S_IRUSR | stat.S_IWUSR)


def _load_credentials() -> Optional[dict]:
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        with open(CREDENTIALS_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _refresh_access_token(creds: dict) -> Optional[dict]:
    if not creds.get("refresh_token"):
        return None
    try:
        resp = requests.post(
            _realm_url("/protocol/openid-connect/token"),
            data={
                "grant_type": "refresh_token",
                "client_id": KEYCLOAK_CLIENT_ID,
                "refresh_token": creds["refresh_token"],
            },
            timeout=10,
        )
        resp.raise_for_status()
        token_response = resp.json()
        _save_credentials(token_response)
        return _load_credentials()
    except requests.exceptions.RequestException:
        return None


def get_access_token() -> Optional[str]:
    """Return a currently-valid access token, refreshing if needed. None if not logged in."""
    creds = _load_credentials()
    if not creds:
        return None
    if time.time() >= creds.get("expires_at", 0) - 15:
        creds = _refresh_access_token(creds)
        if not creds:
            return None
    return creds["access_token"]


def login():
    """Log in via the OAuth2 Device Authorization Grant (like `az login` / `gh auth login`)."""
    try:
        resp = requests.post(
            _realm_url("/protocol/openid-connect/auth/device"),
            data={"client_id": KEYCLOAK_CLIENT_ID},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ Could not start device login: {e}")
        sys.exit(1)

    device = resp.json()
    verification_uri_complete = device.get("verification_uri_complete", device["verification_uri"])
    interval = device.get("interval", 5)
    expires_in = device.get("expires_in", 600)

    print(f"🔐 To sign in, open: {verification_uri_complete}")
    print(f"   (or go to {device['verification_uri']} and enter code: {device['user_code']})")
    webbrowser.open(verification_uri_complete)

    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        resp = requests.post(
            _realm_url("/protocol/openid-connect/token"),
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": KEYCLOAK_CLIENT_ID,
                "device_code": device["device_code"],
            },
            timeout=10,
        )
        if resp.status_code == 200:
            _save_credentials(resp.json())
            print("✅ Logged in successfully")
            return
        error = resp.json().get("error")
        if error == "authorization_pending":
            continue
        elif error == "slow_down":
            interval += 5
            continue
        else:
            print(f"❌ Login failed: {error}")
            sys.exit(1)

    print("❌ Login timed out. Please try again.")
    sys.exit(1)


def logout():
    """Remove cached credentials."""
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()
        print("✅ Logged out")
    else:
        print("ℹ️  Not logged in")


def whoami():
    """Show the currently logged-in user, decoded from the cached token (no signature check)."""
    token = get_access_token()
    if not token:
        print("ℹ️  Not logged in. Run `teams-cli login` first.")
        return
    try:
        import base64
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        print("❌ Could not decode cached token")
        return
    roles = payload.get("realm_access", {}).get("roles", [])
    print(f"👤 {payload.get('preferred_username', 'unknown')}")
    print(f"🔑 Roles: {', '.join(roles) if roles else 'none'}")


class TeamsAPI:
    def __init__(self, base_url: str = API_BASE_URL):
        self.base_url = base_url
        
    def _make_request(self, method: str, endpoint: str, data: Optional[dict] = None) -> dict:
        """Make HTTP request to the API"""
        url = f"{self.base_url}{endpoint}"
        headers = {"Content-Type": "application/json"}
        token = get_access_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            if method == "GET":
                response = requests.get(url, headers=headers)
            elif method == "POST":
                response = requests.post(url, json=data, headers=headers)
            elif method == "DELETE":
                response = requests.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported method: {method}")
                
            response.raise_for_status()
            return response.json()
        except requests.exceptions.ConnectionError:
            print(f"❌ Error: Could not connect to API at {self.base_url}")
            print("   Make sure the Teams API is running")
            sys.exit(1)
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                print("❌ Error: Not authenticated. Run `teams-cli login` first.")
            elif response.status_code == 403:
                print("❌ Error: You don't have permission to do that (team-leader or admin role required).")
            elif response.status_code == 400:
                error_detail = response.json().get("detail", "Bad request")
                print(f"❌ Error: {error_detail}")
            elif response.status_code == 404:
                print("❌ Error: Team not found")
            else:
                print(f"❌ HTTP Error {response.status_code}: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            sys.exit(1)


    def health_check(self):
        """Check API health"""
        result = self._make_request("GET", "/health")
        status = result.get("status", "unknown")
        teams_count = result.get("teams_count", 0)
        print(f"✅ API Status: {status}")
        print(f"📊 Teams Count: {teams_count}")

    def create_team(self, name: str):
        """Create a new team"""
        result = self._make_request("POST", "/teams", {"name": name})
        print(f"✅ Created team: {result['name']}")
        print(f"🆔 Team ID: {result['id']}")
        print(f"📅 Created: {result['created_at']}")

    def list_teams(self):
        """List all teams"""
        teams = self._make_request("GET", "/teams")
        if not teams:
            print("📭 No teams found")
            return
            
        print(f"📋 Found {len(teams)} team(s):")
        print("-" * 60)
        for team in teams:
            print(f"🏷️  Name: {team['name']}")
            print(f"🆔 ID: {team['id']}")
            print(f"📅 Created: {team['created_at']}")
            print("-" * 60)

    def get_team(self, team_id: str):
        """Get a specific team by ID"""
        team = self._make_request("GET", f"/teams/{team_id}")
        print(f"🏷️  Name: {team['name']}")
        print(f"🆔 ID: {team['id']}")
        print(f"📅 Created: {team['created_at']}")

    def delete_team(self, team_id: str):
        """Delete a team"""
        result = self._make_request("DELETE", f"/teams/{team_id}")
        print(f"✅ {result['message']}")

def main():
    parser = argparse.ArgumentParser(
        description="Teams CLI - Manage teams via the Teams API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  teams-cli login                     # Sign in via your browser (device login)
  teams-cli whoami                    # Show who you're logged in as
  teams-cli health                    # Check API health
  teams-cli create "Backend Team"     # Create a new team
  teams-cli list                      # List all teams
  teams-cli get <team-id>            # Get specific team
  teams-cli delete <team-id>         # Delete a team
  teams-cli logout                    # Clear cached credentials
        """
    )
    
    parser.add_argument(
        "--url", 
        default=API_BASE_URL,
        help=f"API base URL (default: {API_BASE_URL})"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Login/logout/whoami commands
    subparsers.add_parser("login", help="Sign in via device login (opens a browser)")
    subparsers.add_parser("logout", help="Clear cached credentials")
    subparsers.add_parser("whoami", help="Show the currently logged-in user")

    # Health command
    subparsers.add_parser("health", help="Check API health")
    
    # Create command
    create_parser = subparsers.add_parser("create", help="Create a new team")
    create_parser.add_argument("name", help="Team name")
    
    # List command
    subparsers.add_parser("list", help="List all teams")
    
    # Get command
    get_parser = subparsers.add_parser("get", help="Get a specific team")
    get_parser.add_argument("team_id", help="Team ID")
    
    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a team")
    delete_parser.add_argument("team_id", help="Team ID")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return

    # Execute command
    try:
        if args.command == "login":
            login()
            return
        elif args.command == "logout":
            logout()
            return
        elif args.command == "whoami":
            whoami()
            return

        # Initialize API client
        api = TeamsAPI(args.url)

        if args.command == "health":
            api.health_check()
        elif args.command == "create":
            api.create_team(args.name)
        elif args.command == "list":
            api.list_teams()
        elif args.command == "get":
            api.get_team(args.team_id)
        elif args.command == "delete":
            api.delete_team(args.team_id)
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
        sys.exit(0)

if __name__ == "__main__":
    main()
