"""
Google Calendar MCP Server (Python)

Exposes MCP tools to create/list/delete Google Calendar events.

Prereqs
-------
1) In Google Cloud Console, enable **Google Calendar API** and create an OAuth 2.0
   **Desktop** client. Download it as `credentials.json` and place beside this file.
2) Install deps (Python 3.10+ recommended):

    pip install mcp google-api-python-client google-auth-httplib2 \
                google-auth-oauthlib python-dateutil

3) First run will open a browser for OAuth; a `token.json` will be saved for reuse.

How to run (stdio transport)
---------------------------
    python gcal_mcp_server.py stdio

Tools exposed
-------------
- create_event(title, start, end, timezone?, description?, location?, attendees?,
               calendar_id?, make_meet_link?, send_updates?) → dict
- cancel_event(event_id, calendar_id?, send_updates?) → str
- list_upcoming(max_results?, calendar_id?) → list[dict]

Notes
-----
- `start` and `end` accept ISO 8601 (e.g., "2025-09-03T10:00:00") or natural variants;
  they are parsed and normalized.
- Set timezone (default "Asia/Colombo").
- When `make_meet_link=True`, a Google Meet link is generated.
- Attendees: pass a list of emails; Google may send invites depending on `send_updates`.
"""
from __future__ import annotations

import os
import uuid
from typing import Optional, List
from datetime import datetime, timezone

from dateutil import parser as dateparser

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from mcp.server.fastmcp import FastMCP

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
DEFAULT_CREDENTIALS_PATH = os.getenv("GCAL_CREDENTIALS_PATH", "credentials.json")
DEFAULT_TOKEN_PATH = os.getenv("GCAL_TOKEN_PATH", "token.json")
DEFAULT_CALENDAR_ID = os.getenv("GCAL_CALENDAR_ID", "primary")


def _get_calendar_service():
    """Authorize and return a Calendar v3 service client."""
    creds: Optional[Credentials] = None

    if os.path.exists(DEFAULT_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(DEFAULT_TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(DEFAULT_CREDENTIALS_PATH):
                raise RuntimeError(
                    "credentials.json not found. Place your OAuth client file beside this script "
                    "or set GCAL_CREDENTIALS_PATH."
                )
            flow = InstalledAppFlow.from_client_secrets_file(DEFAULT_CREDENTIALS_PATH, SCOPES)
            # Opens a local browser window for OAuth
            creds = flow.run_local_server(port=0)
        # Persist token for future runs
        with open(DEFAULT_TOKEN_PATH, "w") as token:
            token.write(creds.to_json())

    # cache_discovery=False avoids a deprecation warning
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


mcp = FastMCP("gcal-mcp")


@mcp.tool()
def create_event(
        title: str,
        start: str,
        end: str,
        timezone: str = "Asia/Colombo",
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        calendar_id: str = DEFAULT_CALENDAR_ID,
        make_meet_link: bool = True,
        send_updates: str = "all",
) -> dict:
    """
    Create a Google Calendar event.

    Args:
        title: Event title
        start: ISO8601-like start (e.g., "2025-09-03T10:00:00")
        end: ISO8601-like end (must be after start)
        timezone: IANA tz (e.g., "Asia/Colombo")
        description: Optional description/agenda
        location: Optional location text
        attendees: Optional list of attendee email addresses
        calendar_id: Calendar to insert into (default "primary")
        make_meet_link: If True, attach a Google Meet link
        send_updates: "all" | "externalOnly" | "none" (invite email behavior)

    Returns: Minimal event details including id, htmlLink, hangoutLink (if any), start, end
    """
    svc = _get_calendar_service()

    # Parse to aware datetimes; if tz-naive, assume provided timezone when serializing
    start_dt = dateparser.parse(start)
    end_dt = dateparser.parse(end)
    if end_dt <= start_dt:
        raise ValueError("end must be after start")

    event: dict = {
        "summary": title,
        "description": description,
        "location": location,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": timezone},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": timezone},
    }

    if attendees:
        event["attendees"] = [{"email": e} for e in attendees]

    if make_meet_link:
        event["conferenceData"] = {"createRequest": {"requestId": str(uuid.uuid4())}}

    created = (
        svc.events()
        .insert(
            calendarId=calendar_id,
            body=event,
            conferenceDataVersion=1,  # needed for Meet link creation
            sendUpdates=send_updates,
        )
        .execute()
    )

    # Try to surface a Meet URL if available
    meet_url = created.get("hangoutLink")
    if not meet_url:
        cd = created.get("conferenceData")
        if cd and "entryPoints" in cd and cd["entryPoints"]:
            meet_url = cd["entryPoints"][0].get("uri")

    return {
        "id": created.get("id"),
        "htmlLink": created.get("htmlLink"),
        "hangoutLink": meet_url,
        "start": created.get("start"),
        "end": created.get("end"),
    }


@mcp.tool()
def cancel_event(
        event_id: str,
        calendar_id: str = DEFAULT_CALENDAR_ID,
        send_updates: str = "all",
) -> str:
    """Cancel (delete) an event by its id."""
    svc = _get_calendar_service()
    svc.events().delete(calendarId=calendar_id, eventId=event_id, sendUpdates=send_updates).execute()
    return f"Deleted event {event_id}"


@mcp.tool()
def list_upcoming(max_results: int = 10, calendar_id: str = DEFAULT_CALENDAR_ID) -> list[dict]:
    """List upcoming events (soonest first)."""
    svc = _get_calendar_service()
    now_iso = datetime.now(timezone.utc).isoformat()
    resp = (
        svc.events()
        .list(
            calendarId=calendar_id,
            timeMin=now_iso,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return resp.get("items", [])


if __name__ == "__main__":
    # FastMCP reads the transport (e.g., stdio) from argv
    mcp.run()

# > npx @modelcontextprotocol/inspector  uv   --directory .  run   E:\\<>\\<>\\gcal_mcp_server.py