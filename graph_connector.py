"""
Microsoft Graph dedicated mailbox connector.

V10.20.21:
- Microsoft Entra ID app-only/client-credentials authentication via MSAL.
- Dedicated mailbox access through Microsoft Graph.
- No Outlook desktop/COM dependency when GRAPH_MAILBOX is configured.
- Paged mailbox reads with retry/backoff for throttling/transient Graph errors.
- Full message metadata/body needed by the existing PDL intelligence pipeline.
- Recursive nested PDL/group membership discovery on initial PDL sync only.
- Mailbox access test uses Mail.Read-compatible mailFolder access rather than
  requiring User.Read.All.
"""

from __future__ import annotations

import os
import time
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import msal
import requests
from cryptography.fernet import Fernet



class GraphMailboxConfig:
    """Encrypted local configuration for the dedicated Graph mailbox.

    UI-managed configuration takes precedence over legacy environment variables.
    The client secret is never returned to templates or API responses.
    """

    CONFIG_DIR = Path(__file__).resolve().parent / "instance" / "config"
    CONFIG_FILE = CONFIG_DIR / "graph_mailbox.json"
    KEY_FILE = CONFIG_DIR / ".graph_mailbox.key"

    @classmethod
    def _ensure_dir(cls):
        cls.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _cipher(cls):
        cls._ensure_dir()
        if not cls.KEY_FILE.exists():
            cls.KEY_FILE.write_bytes(Fernet.generate_key())
            try:
                os.chmod(cls.KEY_FILE, 0o600)
            except Exception:
                pass
        return Fernet(cls.KEY_FILE.read_bytes())

    @classmethod
    def exists(cls):
        return cls.CONFIG_FILE.exists()

    @classmethod
    def save(cls, tenant_id, client_id, client_secret, mailbox):
        cls._ensure_dir()
        cipher = cls._cipher()
        payload = {
            "tenant_id": tenant_id.strip(),
            "client_id": client_id.strip(),
            "mailbox": mailbox.strip(),
            "client_secret": cipher.encrypt(client_secret.encode("utf-8")).decode("utf-8"),
        }
        cls.CONFIG_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.chmod(cls.CONFIG_FILE, 0o600)
        except Exception:
            pass

    @classmethod
    def load(cls):
        if not cls.CONFIG_FILE.exists():
            return None
        payload = json.loads(cls.CONFIG_FILE.read_text(encoding="utf-8"))
        cipher = cls._cipher()
        payload["client_secret"] = cipher.decrypt(
            payload["client_secret"].encode("utf-8")
        ).decode("utf-8")
        return payload

    @classmethod
    def public(cls):
        data = cls.load()
        if not data:
            return None
        return {
            "tenant_id": data.get("tenant_id", ""),
            "client_id": data.get("client_id", ""),
            "mailbox": data.get("mailbox", ""),
            "configured": True,
        }

    @classmethod
    def clear(cls):
        if cls.CONFIG_FILE.exists():
            cls.CONFIG_FILE.unlink()


def graph_config():
    """Return effective Graph configuration.

    Stored UI configuration is preferred. Environment variables remain a
    backward-compatible migration path for existing installations.
    """
    try:
        stored = GraphMailboxConfig.load()
        if stored:
            return stored
    except Exception:
        # Do not make an old installation unusable merely because its local
        # encrypted configuration is damaged; the env fallback can still work.
        pass

    return {
        "tenant_id": os.getenv("GRAPH_TENANT_ID", "").strip(),
        "client_id": os.getenv("GRAPH_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("GRAPH_CLIENT_SECRET", "").strip(),
        "mailbox": os.getenv("GRAPH_MAILBOX", "").strip(),
    }

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _parse_dt(value: Any) -> Optional[datetime]:
    """Convert a Graph ISO timestamp to the application's local wall-clock.

    The existing database conversion path calls dt_utc() and expects a naive
    Outlook-style local datetime. Graph returns UTC/offset-aware timestamps, so
    convert them to the configured operational timezone and remove tzinfo here.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except Exception:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    tz_name = os.getenv("OUTLOOK_TIMEZONE", "Asia/Kolkata").strip() or "UTC"
    try:
        local = dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        local = dt.astimezone(timezone.utc)
    return local.replace(tzinfo=None)


class GraphPropertyAccessor:
    """Small MAPI PropertyAccessor compatibility layer."""

    def __init__(self, message):
        self.message = message

    def GetProperty(self, prop):
        key = str(prop).lower()
        if key in {
            "http://schemas.microsoft.com/mapi/proptag/0x39fe001e",
            "http://schemas.microsoft.com/mapi/proptag/0x39fe001f",
        }:
            return getattr(self.message, "smtp_address", "")
        if key in {
            "http://schemas.microsoft.com/mapi/proptag/0x1035001e",
            "http://schemas.microsoft.com/mapi/proptag/0x1035001f",
        }:
            return getattr(self.message, "internet_message_id", "")
        if key in {
            "http://schemas.microsoft.com/mapi/proptag/0x1042001e",
            "http://schemas.microsoft.com/mapi/proptag/0x1042001f",
        }:
            return getattr(self.message, "in_reply_to", "")
        if hasattr(self.message, "smtp_address"):
            return self.message.smtp_address
        raise KeyError(prop)


class GraphRecipient:
    def __init__(self, address: str, name: str = "", recipient_type: int = 1):
        self.Address = address or ""
        self.Name = name or address or ""
        self.Type = recipient_type
        self.smtp_address = address or ""
        self.PropertyAccessor = GraphPropertyAccessor(self)


class GraphMessage:
    """Outlook-MailItem-compatible representation of a Graph message."""

    def __init__(self, raw: Dict[str, Any], sent: bool = False):
        self.raw = raw
        self._sent = sent

        sender = ((raw.get("sender") or raw.get("from") or {}).get("emailAddress") or {})
        self._sender_email = (sender.get("address") or "").strip().lower()
        self._sender_name = (sender.get("name") or self._sender_email).strip()

        self.EntryID = raw.get("id") or ""
        self.Subject = raw.get("subject") or ""
        self.Body = ((raw.get("body") or {}).get("content") or "")
        self.BodyPreview = raw.get("bodyPreview") or ""
        self.SenderName = self._sender_name
        self.SenderEmailAddress = self._sender_email
        self.SentOn = _parse_dt(raw.get("sentDateTime"))
        self.ReceivedTime = _parse_dt(raw.get("receivedDateTime")) or self.SentOn
        self.CreatedDateTime = _parse_dt(raw.get("createdDateTime"))
        self.LastModifiedDateTime = _parse_dt(raw.get("lastModifiedDateTime"))
        self.ConversationID = raw.get("conversationId") or ""
        self.ParentFolderId = raw.get("parentFolderId") or ""
        self.WebLink = raw.get("webLink") or ""
        self.Importance = raw.get("importance") or "normal"
        self.IsRead = bool(raw.get("isRead", False))
        self.IsDraft = bool(raw.get("isDraft", False))
        self.HasAttachments = bool(raw.get("hasAttachments", False))
        self.Categories = "; ".join(raw.get("categories") or [])
        self.MessageClass = "IPM.Note"
        self.Class = 43  # Outlook olMail = 43

        self.CC = self._recipient_string(raw.get("ccRecipients") or [])
        self.To = self._recipient_string(raw.get("toRecipients") or [])
        self.BCC = self._recipient_string(raw.get("bccRecipients") or [])
        self.ReplyTo = self._recipient_string(raw.get("replyTo") or [])
        self.Recipients = self._recipient_objects(raw)
        self.Sender = GraphRecipient(self._sender_email, self._sender_name, 1)
        self.PropertyAccessor = GraphPropertyAccessor(self)

        headers = {}
        for header in raw.get("internetMessageHeaders") or []:
            name = str(header.get("name") or "").lower()
            value = str(header.get("value") or "")
            if name:
                headers[name] = value

        self.internet_message_id = (
            raw.get("internetMessageId")
            or headers.get("message-id")
            or ""
        )
        self.in_reply_to = (
            headers.get("in-reply-to")
            or headers.get("references")
            or ""
        )

    @staticmethod
    def _recipient_string(values):
        parts = []
        for item in values:
            ea = item.get("emailAddress") or {}
            address = (ea.get("address") or "").strip()
            name = (ea.get("name") or "").strip()
            if address:
                parts.append(
                    f"{name} <{address}>"
                    if name and name.lower() != address.lower()
                    else address
                )
        return "; ".join(parts)

    @staticmethod
    def _recipient_objects(raw):
        result = []
        for item in raw.get("toRecipients") or []:
            ea = item.get("emailAddress") or {}
            result.append(GraphRecipient(ea.get("address") or "", ea.get("name") or "", 1))
        for item in raw.get("ccRecipients") or []:
            ea = item.get("emailAddress") or {}
            result.append(GraphRecipient(ea.get("address") or "", ea.get("name") or "", 2))
        for item in raw.get("bccRecipients") or []:
            ea = item.get("emailAddress") or {}
            result.append(GraphRecipient(ea.get("address") or "", ea.get("name") or "", 3))
        return result


class GraphItems(list):
    def Sort(self, *_args, **_kwargs):
        return None


class GraphFolder:
    def __init__(self, name: str, messages: Iterable[GraphMessage], entry_id: str):
        self.Name = name
        self.DisplayName = name
        self.EntryID = entry_id
        self.FolderPath = name
        self.Items = GraphItems(messages)
        self.Folders = []
        self.Parent = None


class GraphStore:
    def __init__(self, mailbox: str, inbox: GraphFolder, sent: GraphFolder):
        self.DisplayName = mailbox
        self.Name = mailbox
        self._inbox = inbox
        self._sent = sent
        self._root = GraphFolder(mailbox, [], f"store:{mailbox}")
        self._inbox.Parent = self._root
        self._sent.Parent = self._root
        self._root.Folders = [self._inbox, self._sent]

    def GetRootFolder(self):
        return self._root


class GraphStores(list):
    @property
    def Count(self):
        return len(self)

    def Item(self, index):
        return self[index - 1]


class GraphNamespace:
    """Namespace facade used by the existing sync pipeline."""

    def __init__(self, client):
        self.client = client

        # The existing sync pipeline expects Inbox and Sent Items to be
        # available as Outlook-like folders. Graph performs the actual mailbox
        # reads; no Outlook client/profile is opened.
        inbox = GraphFolder("Inbox", client.fetch_messages("inbox"), "graph:inbox")
        sent = GraphFolder("Sent Items", client.fetch_messages("sentitems"), "graph:sentitems")
        self.store = GraphStore(client.mailbox, inbox, sent)
        self.Stores = GraphStores([self.store])

    def GetDefaultFolder(self, folder_type):
        if int(folder_type) == 6:   # Inbox
            return self.store._inbox
        if int(folder_type) == 5:   # Sent Items
            return self.store._sent
        raise KeyError(folder_type)

    def CreateRecipient(self, address):
        return GraphRecipient(address, address, 1)


class GraphMailClient:
    def __init__(self, tenant_id=None, client_id=None, client_secret=None, mailbox=None):
        cfg = graph_config()
        self.tenant_id = (tenant_id if tenant_id is not None else cfg.get("tenant_id", "")).strip()
        self.client_id = (client_id if client_id is not None else cfg.get("client_id", "")).strip()
        self.client_secret = client_secret if client_secret is not None else cfg.get("client_secret", "")
        self.mailbox = (mailbox if mailbox is not None else cfg.get("mailbox", "")).strip()

        missing = [
            key for key, value in {
                "GRAPH_TENANT_ID": self.tenant_id,
                "GRAPH_CLIENT_ID": self.client_id,
                "GRAPH_CLIENT_SECRET": self.client_secret,
                "GRAPH_MAILBOX": self.mailbox,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError("Missing Graph configuration: " + ", ".join(missing))

        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority,
        )
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Prefer": 'outlook.body-content-type="text"',
        })
        self._token = None
        self._token_expires_at = 0.0

    @property
    def encoded_mailbox(self):
        return quote(self.mailbox, safe="@._+-")

    def token(self):
        now = time.time()
        if self._token and now < self._token_expires_at - 120:
            return self._token

        # This is intentionally the same app-only flow as the supplied working
        # mailbox test code. No interactive login and no Outlook account cache.
        result = self.app.acquire_token_silent(GRAPH_SCOPE, account=None)
        if not result:
            result = self.app.acquire_token_for_client(scopes=GRAPH_SCOPE)

        if "access_token" not in result:
            raise RuntimeError(
                "Microsoft Graph authentication failed: "
                f"{result.get('error')}: {result.get('error_description')}"
            )

        self._token = result["access_token"]
        self._token_expires_at = now + int(result.get("expires_in", 3600))
        return self._token

    def request(self, method: str, url: str, params=None):
        last_error = None

        for attempt in range(1, 5):
            headers = {
                "Authorization": "Bearer " + self.token(),
                "Accept": "application/json",
                "Prefer": 'outlook.body-content-type="text"',
            }

            try:
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    timeout=90,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 4:
                    raise RuntimeError(f"Graph network request failed: {exc}") from exc
                time.sleep(min(2 ** (attempt - 1), 8))
                continue

            if response.ok:
                return response.json()

            if response.status_code == 401 and attempt < 4:
                # Force a fresh token after an expired/invalid cached token.
                self._token = None
                self._token_expires_at = 0
                last_error = response.text
                continue

            if response.status_code in _RETRYABLE_STATUS and attempt < 4:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = max(1, min(int(retry_after), 30))
                except Exception:
                    delay = min(2 ** (attempt - 1), 15)
                time.sleep(delay)
                last_error = response.text
                continue

            raise RuntimeError(
                f"Graph API {method} {url} failed: "
                f"{response.status_code} {response.text[:1500]}"
            )

        raise RuntimeError(f"Graph request failed after retries: {last_error}")

    def get_paged(self, url, params=None):
        values = []
        first = True
        while url:
            data = self.request("GET", url, params=params if first else None)
            first = False
            values.extend(data.get("value") or [])
            url = data.get("@odata.nextLink")
        return values

    def fetch_messages(self, folder="inbox", top=999):
        """Read all messages in the configured scan window, following paging."""
        folder = folder.strip("/")
        url = (
            f"{GRAPH_BASE}/users/{self.encoded_mailbox}/mailFolders/"
            f"{quote(folder, safe='')}/messages"
        )

        days = int(os.getenv("SYNC_DAYS", "7") or 7)
        date_field = "receivedDateTime" if folder.lower() == "inbox" else "sentDateTime"

        params = {
            "$top": min(max(int(top), 1), 999),
            "$select": (
                "id,internetMessageId,subject,body,bodyPreview,from,sender,"
                "toRecipients,ccRecipients,bccRecipients,replyTo,"
                "receivedDateTime,sentDateTime,createdDateTime,lastModifiedDateTime,"
                "conversationId,internetMessageHeaders,parentFolderId,webLink,"
                "importance,isRead,isDraft,hasAttachments,categories"
            ),
            "$orderby": (
                "receivedDateTime DESC"
                if folder.lower() == "inbox"
                else "sentDateTime DESC"
            ),
        }

        # SYNC_DAYS=0 explicitly means no Graph date filter. Otherwise Graph
        # performs the coarse server-side filter and the existing sync layer
        # applies its incremental overlap/cutoff as a second safety boundary.
        if days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            params["$filter"] = (
                f"{date_field} ge "
                f"{cutoff.isoformat().replace('+00:00', 'Z')}"
            )

        return [
            GraphMessage(item, sent=(folder.lower() == "sentitems"))
            for item in self.get_paged(url, params)
        ]

    def test_connection(self):
        """Test mailbox access using a Mail.Read-compatible endpoint."""
        url = (
            f"{GRAPH_BASE}/users/{self.encoded_mailbox}/mailFolders/inbox"
        )
        return self.request(
            "GET",
            url,
            params={
                "$select": "id,displayName,totalItemCount,unreadItemCount"
            },
        )

    def find_group_by_mail(self, address):
        address = (address or "").strip().lower()
        if not address:
            return None

        safe_address = address.replace("'", "''")
        data = self.request(
            "GET",
            f"{GRAPH_BASE}/groups",
            params={
                "$filter": f"mail eq '{safe_address}'",
                "$select": "id,displayName,mail,mailEnabled,securityEnabled",
                "$top": 10,
            },
        )
        values = data.get("value") or []
        return values[0] if values else None

    def group_members_recursive(
        self,
        group_id,
        visited=None,
        depth=0,
        max_depth=8,
    ):
        """Expand users and nested groups recursively, de-duplicating users."""
        if visited is None:
            visited = set()
        if not group_id or group_id in visited or depth > max_depth:
            return []

        visited.add(group_id)
        url = f"{GRAPH_BASE}/groups/{group_id}/members"
        params = {
            "$select": "id,displayName,mail,userPrincipalName,@odata.type",
            "$top": 999,
        }

        result = []
        for item in self.get_paged(url, params):
            otype = str(item.get("@odata.type") or "").lower()
            if otype.endswith("group"):
                result.extend(
                    self.group_members_recursive(
                        item.get("id"),
                        visited,
                        depth + 1,
                        max_depth,
                    )
                )
                continue

            email = (
                item.get("mail")
                or item.get("userPrincipalName")
                or ""
            ).strip().lower()
            if "@" not in email:
                continue

            result.append({
                "email": email,
                "name": (item.get("displayName") or email).strip(),
            })

        dedup = {}
        for item in result:
            dedup[item["email"]] = item
        return list(dedup.values())

    def discover_pdl_members(self, pdl_address):
        group = self.find_group_by_mail(pdl_address)
        if not group:
            return []
        return self.group_members_recursive(group.get("id"))


def graph_enabled():
    """Return True when the UI-managed or legacy Graph mailbox is configured."""
    cfg = graph_config()
    return bool(
        cfg.get("tenant_id")
        and cfg.get("client_id")
        and cfg.get("client_secret")
        and cfg.get("mailbox")
    )


def graph_connect():
    client = GraphMailClient()
    client.test_connection()
    return client, GraphNamespace(client)
