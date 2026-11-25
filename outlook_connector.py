import os
import re
import time
import json
try:
    import pythoncom
    import win32com.client
except Exception:  # Graph-only deployments do not require Outlook/COM.
    pythoncom = None
    win32com = None
from sqlalchemy import func

from graph_connector import graph_enabled, graph_connect, graph_config

from datetime import datetime, timezone, timedelta

from db import (
    db,
    list_pdls,
    upsert_email,
    set_sync_state,
    get_sync_state,
    Engineer,
    Email,
    TeamGroup,
    Alert,
    create_alert,
    log_event,
)

from intelligence import (
    build_intelligence,
    detect_schedule_miss,
    INTERNAL_DOMAINS,
)


# ============================================================
# CONFIGURATION
# ============================================================

MAILBOX = os.getenv(
    "OUTLOOK_MAILBOX",
    ""
).strip()

SYNC_DAYS = int(
    os.getenv(
        "SYNC_DAYS",
        "7",
    )
)

SYNC_LIMIT = int(
    os.getenv(
        "SYNC_LIMIT",
        "1000",
    )
)

SYNC_BATCH_SIZE = int(
    os.getenv(
        "SYNC_BATCH_SIZE",
        "10",
    )
)

# Re-scan a rolling overlap on every incremental sync. This is deliberately
# larger than the old 15-minute window so messages that arrived during a
# transient Outlook/SQLite/COM failure are recovered automatically.
SYNC_OVERLAP_MINUTES = int(
    os.getenv(
        "SYNC_OVERLAP_MINUTES",
        "120",
    )
)

# Performance controls: discovery/correlation is cached once per sync rather than
# repeated for every email. These defaults preserve the reliability behavior while
# avoiding expensive Outlook/SQLAlchemy work inside the message loop.
SYNC_MEMBER_DISCOVERY_ONCE = os.getenv("SYNC_MEMBER_DISCOVERY_ONCE", "1").strip().lower() not in {"0", "false", "no"}
SYNC_TIMING_LOG = os.getenv("SYNC_TIMING_LOG", "1").strip().lower() not in {"0", "false", "no"}


# ============================================================
# NOTIFICATION DATE POLICY
# ============================================================

def _email_received_today_ist(received_at):
    """Current-day notification gate. Notifications are about emails received
    today in IST, not alerts created today and not old mail reprocessed today."""
    try:
        from zoneinfo import ZoneInfo
        ist = ZoneInfo("Asia/Kolkata")
        dt = received_at if received_at and received_at.tzinfo else (
            received_at.replace(tzinfo=timezone.utc) if received_at else None
        )
        if not dt:
            return False
        return dt.astimezone(ist).date() == datetime.now(timezone.utc).astimezone(ist).date()
    except Exception:
        return False


# ============================================================
# MAPI PROPERTIES
# ============================================================

PR_SMTP_ADDRESS_A = (
    "http://schemas.microsoft.com/mapi/proptag/0x39FE001E"
)

PR_SMTP_ADDRESS_W = (
    "http://schemas.microsoft.com/mapi/proptag/0x39FE001F"
)

PR_INTERNET_MESSAGE_ID = (
    "http://schemas.microsoft.com/mapi/proptag/0x1035001E"
)


# ============================================================
# OUTLOOK CONSTANTS
# ============================================================

OL_FOLDER_INBOX = 6
OL_FOLDER_SENT = 5

OL_MAIL_ITEM = 43

OL_RECIPIENT_TO = 1
OL_RECIPIENT_CC = 2


# ============================================================
# TEXT HELPERS
# ============================================================

def text(value):

    try:
        return str(value or "")
    except Exception:
        return ""


def normalize_name(value):

    value = (
        text(value)
        .strip()
        .lower()
    )

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def is_archive(name):

    n = normalize_name(name)

    return any(
        x in n
        for x in (
            "online archive",
            "in-place archive",
            "inplace archive",
        )
    )


def safe_get(
    obj,
    name,
    default=None,
):

    try:
        return getattr(
            obj,
            name,
        )
    except Exception:
        return default


# ============================================================
# EMAIL EXTRACTION
# ============================================================

def extract_email_addresses(value):

    matches = re.findall(
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        text(value),
    )

    return list(
        dict.fromkeys(
            x.lower()
            for x in matches
        )
    )


def smtp_from_property(obj):

    if obj is None:
        return ""

    for prop in (
        PR_SMTP_ADDRESS_W,
        PR_SMTP_ADDRESS_A,
    ):

        try:

            value = text(
                obj.PropertyAccessor.GetProperty(
                    prop
                )
            ).strip().lower()

            if "@" in value:
                return value

        except Exception:
            pass

    return ""


def sender_smtp(message):

    try:

        smtp = smtp_from_property(
            message.Sender
        )

        if smtp:
            return smtp

    except Exception:
        pass

    value = text(
        safe_get(
            message,
            "SenderEmailAddress",
            "",
        )
    ).lower()

    extracted = extract_email_addresses(
        value
    )

    if extracted:
        return extracted[0]

    if (
        "@" in value
        and not value.startswith("/o=")
    ):
        return value

    return ""


# ============================================================
# RECIPIENTS
# ============================================================

def get_recipient_details(message):

    to_values = []
    cc_values = []

    try:

        raw = text(
            message.To
        ).strip()

        if raw:

            to_values.append(
                normalize_name(raw)
            )

            to_values.extend(
                extract_email_addresses(raw)
            )

    except Exception:
        pass

    try:

        raw = text(
            message.CC
        ).strip()

        if raw:

            cc_values.append(
                normalize_name(raw)
            )

            cc_values.extend(
                extract_email_addresses(raw)
            )

    except Exception:
        pass

    try:

        for recipient in message.Recipients:

            vals = []

            try:

                if recipient.Name:

                    vals.append(
                        normalize_name(
                            recipient.Name
                        )
                    )

            except Exception:
                pass

            try:

                if recipient.Address:

                    vals.append(
                        normalize_name(
                            recipient.Address
                        )
                    )

            except Exception:
                pass

            smtp = smtp_from_property(
                recipient
            )

            if smtp:
                vals.append(smtp)

            for value in list(vals):

                vals.extend(
                    extract_email_addresses(
                        value
                    )
                )

            vals = list(
                dict.fromkeys(
                    x
                    for x in (
                        normalize_name(v)
                        for v in vals
                    )
                    if x
                )
            )

            try:
                recipient_type = int(
                    recipient.Type
                )
            except Exception:
                recipient_type = OL_RECIPIENT_TO

            if recipient_type == OL_RECIPIENT_CC:

                cc_values.extend(vals)

            elif recipient_type == OL_RECIPIENT_TO:

                to_values.extend(vals)

    except Exception:
        pass

    to_values = list(
        dict.fromkeys(
            x
            for x in (
                normalize_name(v)
                for v in to_values
            )
            if x
        )
    )

    cc_values = list(
        dict.fromkeys(
            x
            for x in (
                normalize_name(v)
                for v in cc_values
            )
            if x
        )
    )

    return {
        "to": to_values,
        "cc": cc_values,
        "all": list(
            dict.fromkeys(
                to_values + cc_values
            )
        ),
    }


def recipients(message):

    try:

        raw = text(
            message.To
        ).strip()

        if raw:
            return raw

    except Exception:
        pass

    return "; ".join(
        get_recipient_details(
            message
        )["to"]
    )


# ============================================================
# OUTLOOK DATES
# ============================================================

# Outlook/COM exposes ReceivedTime/SentOn as the mailbox/client-local
# wall-clock value.  Treat that wall-clock as the configured operational
# timezone before storing UTC.  This prevents a machine/COM timezone offset
# from being applied twice (for example 16:00 IST becoming 21:30 IST).
OUTLOOK_TZ_OFFSET_MINUTES = int(os.getenv("OUTLOOK_TIMEZONE_OFFSET_MINUTES", "330"))
OUTLOOK_LOCAL_TZ = timezone(timedelta(minutes=OUTLOOK_TZ_OFFSET_MINUTES))

def dt_utc(value):

    if value is None:
        raise ValueError(
            "datetime is None"
        )

    # Preserve the displayed Outlook wall-clock time and explicitly attach
    # the application's configured operational timezone.  We intentionally
    # do not trust a COM tzinfo object here because some Outlook/Windows
    # combinations expose the same local wall-clock with a misleading or
    # UTC tzinfo, which caused the +05:30 display bug.
    wall_clock = value.replace(tzinfo=None)
    return wall_clock.replace(tzinfo=OUTLOOK_LOCAL_TZ).astimezone(timezone.utc)


def cid(message):

    return text(
        safe_get(
            message,
            "ConversationID",
            "",
        )
    )


# ============================================================
# OUTLOOK CONNECTION
# ============================================================

def connect_outlook():

    # V10.20.20: production mailbox access uses Microsoft Graph app-only
    # authentication. Outlook/COM remains available only when GRAPH_MAILBOX is
    # not configured, preserving the legacy POC path for troubleshooting.
    if graph_enabled():
        client, namespace = graph_connect()
        return client, namespace

    diagnostics = []

    try:

        diagnostics.append(
            "Attempting GetActiveObject(Outlook.Application)"
        )

        outlook = (
            win32com.client
            .GetActiveObject(
                "Outlook.Application"
            )
        )

        diagnostics.append(
            "Connected to existing Outlook instance."
        )

        namespace = (
            outlook.GetNamespace("MAPI")
        )

        diagnostics.append(
            "Successfully obtained MAPI namespace."
        )

        try:

            diagnostics.append(
                f"MAPI store count="
                f"{namespace.Stores.Count}"
            )

        except Exception as exc:

            raise RuntimeError(
                "Outlook connected but MAPI stores "
                f"could not be enumerated: {exc}"
            ) from exc

        return outlook, namespace

    except Exception as active_exc:

        diagnostics.append(
            f"GetActiveObject failed: {active_exc}"
        )

    try:

        diagnostics.append(
            "Attempting Dispatch(Outlook.Application)"
        )

        outlook = (
            win32com.client
            .Dispatch(
                "Outlook.Application"
            )
        )

        diagnostics.append(
            "Outlook Dispatch succeeded."
        )

        namespace = (
            outlook.GetNamespace("MAPI")
        )

        diagnostics.append(
            "MAPI namespace obtained."
        )

        try:

            diagnostics.append(
                f"MAPI store count="
                f"{namespace.Stores.Count}"
            )

        except Exception as exc:

            raise RuntimeError(
                "Outlook started but MAPI Stores "
                f"could not be enumerated: {exc}"
            ) from exc

        return outlook, namespace

    except Exception as dispatch_exc:

        diagnostics.append(
            f"Dispatch failed: {dispatch_exc}"
        )

        raise RuntimeError(
            "Unable to connect to classic Outlook "
            "through Windows COM/MAPI.\n\n"
            + "\n".join(diagnostics)
            + "\n\nPossible causes:\n"
            "1. Classic Outlook is not installed.\n"
            "2. Outlook is not fully initialized.\n"
            "3. Outlook profile/MAPI is unavailable.\n"
            "4. Outlook and Flask are running at different privilege levels.\n"
            "5. COM registration/profile problem.\n"
            "6. New Outlook is being used instead of Classic Outlook."
        ) from dispatch_exc


# ============================================================
# STORE DISCOVERY
# ============================================================

def get_all_stores(namespace):

    stores = []

    for i in range(
        1,
        namespace.Stores.Count + 1,
    ):

        try:

            store = namespace.Stores.Item(i)

            name = text(
                safe_get(
                    store,
                    "DisplayName",
                    "",
                )
            )

            if is_archive(name):
                continue

            stores.append(store)

        except Exception:
            continue

    return stores


def get_store(namespace):

    stores = get_all_stores(
        namespace
    )

    if not stores:

        raise RuntimeError(
            "No non-archive Outlook store was found."
        )

    if MAILBOX:

        requested = normalize_name(
            MAILBOX
        )

        for store in stores:

            name = normalize_name(
                safe_get(
                    store,
                    "DisplayName",
                    "",
                )
            )

            if name == requested:
                return store

        for store in stores:

            name = normalize_name(
                safe_get(
                    store,
                    "DisplayName",
                    "",
                )
            )

            if (
                requested in name
                or name in requested
            ):
                return store

        available = ", ".join(
            text(
                safe_get(
                    store,
                    "DisplayName",
                    "",
                )
            )
            for store in stores
        )

        raise RuntimeError(
            f"OUTLOOK_MAILBOX '{MAILBOX}' was not found.\n"
            f"Available stores: {available}"
        )

    # Prefer the store containing the
    # Outlook default Inbox.
    try:

        default_inbox = (
            namespace.GetDefaultFolder(
                OL_FOLDER_INBOX
            )
        )

        default_parent = normalize_name(
            safe_get(
                default_inbox.Parent,
                "Name",
                "",
            )
        )

        for store in stores:

            store_name = normalize_name(
                safe_get(
                    store,
                    "DisplayName",
                    "",
                )
            )

            if (
                store_name
                and store_name == default_parent
            ):
                return store

    except Exception:
        pass

    if len(stores) == 1:
        return stores[0]

    raise RuntimeError(
        "Multiple non-archive Outlook stores exist. "
        "Set OUTLOOK_MAILBOX explicitly."
    )


# ============================================================
# ROOT FOLDER
# ============================================================

def get_store_root(store):

    try:

        root = store.GetRootFolder()

        if root:
            return root

    except Exception:
        pass

    try:

        root = store.RootFolder

        if root:
            return root

    except Exception:
        pass

    return None


# ============================================================
# FOLDER SEARCH
# ============================================================

def folder_matches(folder, wanted_names):

    folder_name = normalize_name(
        safe_get(
            folder,
            "Name",
            "",
        )
    )

    return folder_name in {
        normalize_name(x)
        for x in wanted_names
    }


def find_folder_recursive(
    folder,
    wanted_names,
    max_depth=10,
    current_depth=0,
):

    if folder is None:
        return None

    if current_depth > max_depth:
        return None

    try:

        if folder_matches(
            folder,
            wanted_names,
        ):
            return folder

    except Exception:
        pass

    try:

        for child in folder.Folders:

            found = find_folder_recursive(
                child,
                wanted_names,
                max_depth,
                current_depth + 1,
            )

            if found is not None:
                return found

    except Exception:
        pass

    return None


def get_special_folder(
    namespace,
    store,
    folder_type,
    folder_names,
):

    # First try Outlook's default folder.
    try:

        folder = namespace.GetDefaultFolder(
            folder_type
        )

        parent = safe_get(
            folder,
            "Parent",
            None,
        )

        parent_name = normalize_name(
            safe_get(
                parent,
                "Name",
                "",
            )
        )

        store_name = normalize_name(
            safe_get(
                store,
                "DisplayName",
                "",
            )
        )

        if (
            not store_name
            or not parent_name
            or parent_name == store_name
        ):
            return folder

    except Exception:
        pass

    # Search actual store tree.
    root = get_store_root(store)

    if root is None:

        raise RuntimeError(
            "Unable to obtain Outlook store root folder."
        )

    found = find_folder_recursive(
        root,
        folder_names,
    )

    if found is not None:
        return found

    root_name = text(
        safe_get(
            root,
            "Name",
            "",
        )
    )

    raise RuntimeError(
        f"Could not find folder "
        f"{folder_names} under Outlook store "
        f"'{root_name}'."
    )


def get_inbox(
    namespace,
    store,
):

    return get_special_folder(
        namespace,
        store,
        OL_FOLDER_INBOX,
        [
            "Inbox",
            "Posteingang",
            "Boîte de réception",
        ],
    )


def get_sent_items(
    namespace,
    store,
):

    return get_special_folder(
        namespace,
        store,
        OL_FOLDER_SENT,
        [
            "Sent Items",
            "Sent",
            "Gesendete Elemente",
            "Éléments envoyés",
        ],
    )


# ============================================================
# FOLDER COLLECTION
# ============================================================

def collect_mail_folders(
    folder,
    path=None,
    output=None,
    visited=None,
):

    if output is None:
        output = []

    if visited is None:
        visited = set()

    if folder is None:
        return output

    try:

        entry_id = text(
            safe_get(
                folder,
                "EntryID",
                "",
            )
        )

        if entry_id and entry_id in visited:
            return output

        if entry_id:
            visited.add(entry_id)

    except Exception:
        pass

    folder_name = text(
        safe_get(
            folder,
            "Name",
            "",
        )
    )

    if path is None:
        path = folder_name

    output.append(
        (
            folder,
            path,
        )
    )

    try:

        for child in folder.Folders:

            child_name = text(
                safe_get(
                    child,
                    "Name",
                    "",
                )
            )

            child_path = (
                f"{path}\\{child_name}"
                if path
                else child_name
            )

            collect_mail_folders(
                child,
                child_path,
                output,
                visited,
            )

    except Exception:
        pass

    return output


# ============================================================
# RECENT ITEMS
# ============================================================

def recent_items(
    folder,
    cutoff,
    limit=0,
):

    output = []

    try:
        items = folder.Items
        sort_succeeded = True
        try:
            items.Sort("[ReceivedTime]", True)
        except Exception:
            # If Outlook cannot sort the collection, we must not use `break`
            # on the cutoff because an older item may appear before newer mail.
            sort_succeeded = False

        for item in items:
            try:
                if safe_get(item, "Class") != OL_MAIL_ITEM:
                    continue
                received_value = safe_get(item, "ReceivedTime", None)
                if received_value is None:
                    continue
                received = dt_utc(received_value)
                if received < cutoff:
                    if sort_succeeded:
                        break
                    continue
                output.append(item)
                if sort_succeeded and limit > 0 and len(output) >= limit:
                    break
            except Exception:
                continue

        # If Outlook did not honor Sort, we had to inspect the complete recent
        # window. Sort the collected subset ourselves before applying the cap so
        # each folder still contributes its newest messages.
        if not sort_succeeded:
            output.sort(key=lambda x: dt_utc(safe_get(x, "ReceivedTime", datetime.min)), reverse=True)
            if limit > 0:
                output = output[:limit]
    except Exception:
        pass

    return output


def collect_recent_inbox_items(
    inbox,
    cutoff,
    limit,
):

    candidates = []
    stats = []
    folders = collect_mail_folders(inbox)

    # We must inspect every folder: stopping after the first folder reaches the
    # global limit can hide newer mail in a later folder. Instead, cap each folder
    # at N newest messages, then take the global N newest across all folders.
    folder_limit = max(1, int(limit)) if limit > 0 else 0

    for folder, path in folders:
        items = recent_items(folder, cutoff, folder_limit)
        stats.append({
            "folder": path,
            "items": len(items),
        })
        for item in items:
            try:
                candidates.append((dt_utc(item.ReceivedTime), path, item))
            except Exception:
                pass

    candidates.sort(key=lambda x: x[0], reverse=True)
    if limit > 0:
        candidates = candidates[:limit]
    return candidates, stats


# ============================================================
# MESSAGE FILTERING
# ============================================================

# Internal EIMS notification sender. These messages are generated by the
# monitoring system itself and must never become customer workload or action
# items. Responses from real engineers remain valid response evidence.
INTERNAL_TOOL_SENDERS = {
    "deployment.management-system@hpe.com",
}

AUTO_REPLY_PATTERNS = [
    "automatic reply", "auto reply", "autoreply", "out of office",
    "out-of-office", "away from the office", "automatic response",
    "delivery status notification", "undeliverable",
]


def is_internal_tool_message(message):
    """Return True for messages generated by this monitoring application."""
    return (sender_smtp(message) or "").strip().lower() in INTERNAL_TOOL_SENDERS


def is_automatic_message(message):
    """Ignore automatic replies/OOO/DSN messages before intelligence scoring."""
    subject = text(safe_get(message, "Subject", "")).lower()
    sender = sender_smtp(message).lower()
    if any(pattern in subject for pattern in AUTO_REPLY_PATTERNS):
        return True
    if sender.startswith(("mailer-daemon@", "postmaster@")):
        return True
    try:
        message_class = text(safe_get(message, "MessageClass", "")).lower()
        if message_class and message_class not in {"ipm.note", "ipm.note.smime"}:
            if "report" in message_class or "meeting" in message_class:
                return True
    except Exception:
        pass
    return False


def is_duplicate_message(message, internet_id):
    """Deduplicate copies delivered through multiple monitored PDLs.

    The same Outlook EntryID is intentionally *not* skipped because the
    existing upsert path refreshes response/TAT state on later syncs.
    """
    if not internet_id:
        return False
    existing = Email.query.filter_by(internet_message_id=internet_id).first()
    if not existing:
        return False
    entry_id = text(safe_get(message, "EntryID", "")).strip()
    return bool(existing.entry_id != entry_id)


# ============================================================
# PDL MATCH
# ============================================================

def find_pdl(
    message,
    pdls,
):

    info = get_recipient_details(
        message
    )

    to_values = set(
        info["to"]
    )

    cc_values = set(
        info["cc"]
    )

    for pdl in pdls:

        address = normalize_name(
            pdl.address
        )

        if (
            address
            and address in to_values
        ):
            return pdl

    for pdl in pdls:

        address = normalize_name(
            pdl.address
        )

        if (
            address
            and address in cc_values
        ):
            return pdl

    for pdl in pdls:

        name = normalize_name(
            pdl.name
        )

        if (
            name
            and name in to_values
        ):
            return pdl

    for pdl in pdls:

        name = normalize_name(
            pdl.name
        )

        if (
            name
            and name in cc_values
        ):
            return pdl

    return None


# ============================================================
# RESPONSE AUTHORIZATION
# ============================================================

def ensure_pdl_team_group(pdl):
    """Create the canonical Team Management group for a PDL."""
    if not pdl:
        return None
    name = f"PDL - {pdl.name}"[:160]
    group = TeamGroup.query.filter(func.lower(TeamGroup.name) == name.lower()).first()
    if group is None:
        group = TeamGroup(name=name, manager_name=None, active=True)
        db.session.add(group)
        db.session.flush()
    if pdl not in (group.pdls or []):
        group.pdls.append(pdl)
    return group


def _address_entry_members(address_entry):
    """Best-effort Exchange DL expansion through the local Outlook profile."""
    members = []
    if address_entry is None:
        return members
    try:
        dl = address_entry.GetExchangeDistributionList()
        collection = getattr(dl, "Members", None)
        if collection is not None:
            for i in range(1, int(collection.Count) + 1):
                try:
                    members.append(collection.Item(i))
                except Exception:
                    continue
    except Exception:
        pass
    if members:
        return members
    try:
        collection = getattr(address_entry, "Members", None)
        if collection is not None:
            for i in range(1, int(collection.Count) + 1):
                try:
                    members.append(collection.Item(i))
                except Exception:
                    continue
    except Exception:
        pass
    return members


def _address_entry_smtp(entry):
    """Extract SMTP from an Outlook AddressEntry/Recipient."""
    for attr in ("Address", "PropertyAccessor"):
        try:
            if attr == "Address":
                value = text(getattr(entry, attr, ""))
                if "@" in value and value.lower().endswith(tuple("abcdefghijklmnopqrstuvwxyz0123456789")):
                    return value.strip().lower()
            else:
                for prop in (PR_SMTP_ADDRESS_W, PR_SMTP_ADDRESS_A):
                    try:
                        value = text(entry.PropertyAccessor.GetProperty(prop)).strip().lower()
                        if "@" in value:
                            return value
                    except Exception:
                        continue
        except Exception:
            continue
    try:
        ae = getattr(entry, "AddressEntry", None)
        if ae is not None and ae is not entry:
            return _address_entry_smtp(ae)
    except Exception:
        pass
    return ""


def discover_pdl_members_from_outlook(namespace, pdl):
    """Discover PDL members.

    Graph mode expands nested PDL/group membership recursively during the
    initial PDL sync. Legacy Outlook mode keeps the existing Exchange DL
    expansion behavior.
    """
    discovered = []
    try:
        if graph_enabled():
            client = getattr(namespace, "client", None)
            if client is None:
                return discovered
            for item in client.discover_pdl_members(pdl.address):
                email = (item.get("email") or "").strip().lower()
                name = (item.get("name") or email).strip()
                if email and "@" in email:
                    domain = email.rsplit("@", 1)[1]
                    if domain not in INTERNAL_DOMAINS:
                        continue
                    local = email.split("@", 1)[0].lower()
                    if local.startswith((
                        "no-reply", "noreply", "mailer-daemon",
                        "postmaster", "do-not-reply", "donotreply",
                    )):
                        continue
                    discovered.append((email, name[:160]))
            return list(dict.fromkeys(discovered))

        recipient = namespace.CreateRecipient(pdl.address)
        recipient.Resolve()
        if not bool(safe_get(recipient, "Resolved", False)):
            return discovered
        entry = safe_get(recipient, "AddressEntry", None)
        for member in _address_entry_members(entry):
            email = _address_entry_smtp(member)
            if not email or "@" not in email:
                continue
            domain = email.rsplit("@", 1)[1]
            if domain not in INTERNAL_DOMAINS:
                continue
            local = email.split("@",1)[0].lower()
            if local.startswith(("no-reply", "noreply", "mailer-daemon", "postmaster", "do-not-reply", "donotreply")):
                continue
            name = text(safe_get(member, "Name", "")) or text(safe_get(member, "Address", "")) or local.replace(".", " ").title()
            discovered.append((email, name[:160]))
    except Exception:
        pass
    return list(dict.fromkeys(discovered))


def _upsert_discovered_member(pdl, email, name, source="outlook_membership"):
    email = (email or "").strip().lower()
    if not email or "@" not in email or not pdl:
        return None, False
    engineer = Engineer.query.filter(func.lower(Engineer.email) == email).first()
    if engineer is None:
        local = email.split("@",1)[0]
        service = any(x in local for x in ("svc", "service", "sa-", "bot", "automation"))
        engineer = Engineer(name=(name or local.replace(".", " ").title())[:160], email=email, team="Auto-discovered", designation=("Service account / PDL member" if service else "Auto-discovered PDL member"), contact_role="engineer", active=True)
        db.session.add(engineer)
        db.session.flush()
    changed = False
    if not engineer_has_pdl_response_authority(engineer, pdl):
        engineer.pdl_access.append(pdl)
        changed = True
    group = ensure_pdl_team_group(pdl)
    if group is not None and engineer not in group.engineers:
        group.engineers.append(engineer)
        changed = True
    return engineer, changed


def auto_discover_pdl_members(message, pdl, response_pool, namespace=None):
    """Discover PDL members from DL expansion plus verified reply evidence."""
    if not pdl:
        return []
    discovered = []
    try:
        ensure_pdl_team_group(pdl)
        if namespace is not None:
            for email, name in discover_pdl_members_from_outlook(namespace, pdl):
                _, changed = _upsert_discovered_member(pdl, email, name, "outlook_membership")
                if changed:
                    discovered.append(email)
        # Strong secondary signal: an internal sender who replied to this exact
        # PDL/customer thread becomes a member only after correlation succeeds.
        for candidate in response_pool or []:
            try:
                sender = (sender_smtp(candidate) or "").strip().lower()
                if not sender or "@" not in sender:
                    continue
                if sender.rsplit("@",1)[1] not in INTERNAL_DOMAINS:
                    continue
                local = sender.split("@",1)[0]
                if local.startswith(("no-reply", "noreply", "mailer-daemon", "postmaster", "do-not-reply", "donotreply")):
                    continue
                received = dt_utc(message.ReceivedTime)
                if not _sent_item_matches(message, candidate, received) and not _engineer_reply_matches(message, candidate, received):
                    continue
                display = text(safe_get(candidate, "SenderName", "")) or local.replace(".", " ").replace("_", " ").title()
                _, changed = _upsert_discovered_member(pdl, sender, display, "verified_reply")
                if changed:
                    discovered.append(sender)
            except Exception:
                continue
        if discovered:
            db.session.commit()
            log_event("INFO", "pdl_member_discovery", f"Automatically discovered {len(set(discovered))} member(s) for PDL {pdl.name}.", ", ".join(sorted(set(discovered))))
        else:
            # Group existence itself is useful; absence of discoverable members
            # is an explicit admin diagnostic instead of silent failure.
            group = ensure_pdl_team_group(pdl)
            if group is not None and not group.engineers:
                log_event("WARN", "pdl_member_discovery", f"PDL member discovery incomplete for {pdl.name}; no members were exposed by the local Outlook profile. Admin manual mapping is required as fallback.", pdl.address)
                db.session.commit()
    except Exception as exc:
        db.session.rollback()
        try:
            log_event("WARN", "pdl_member_discovery", f"PDL member discovery failed for {pdl.name}; manual mapping remains available.", str(exc))
        except Exception:
            pass
    return sorted(set(discovered))


def engineer_has_pdl_response_authority(engineer, pdl):
    """Return True when an engineer is configured to respond for a PDL.

    Team Management PDL access is authoritative. Legacy EngineerGroup mappings
    are also honored so existing installations do not suddenly stop counting
    valid engineer responses after moving to Team Management.
    """
    if not engineer or not pdl:
        return False
    try:
        if pdl in (getattr(engineer, "pdl_access", None) or []):
            return True
    except Exception:
        pass
    try:
        legacy_group = getattr(engineer, "group", None)
        if legacy_group and getattr(legacy_group, "pdl_id", None) == pdl.id and getattr(legacy_group, "active", True):
            return True
    except Exception:
        pass
    try:
        for team_group in (getattr(engineer, "team_groups", None) or []):
            if getattr(team_group, "active", True) and pdl in (getattr(team_group, "pdls", None) or []):
                return True
    except Exception:
        pass
    return False


# ============================================================
# RESPONSE DETECTION
# ============================================================

# Outlook normally preserves ConversationID on replies, but that is not
# guaranteed for every mailbox/client (mobile Outlook, delegated mailboxes,
# imported messages, etc.).  We therefore use a deterministic hierarchy:
#
#   1. InReplyTo -> original Internet Message-ID (strongest match)
#   2. ConversationID + sent time
#   3. Normalized subject + original sender is a recipient of the sent mail
#
# This prevents a real reply from remaining "Waiting" simply because Outlook
# changed/lost the conversation identifier.

PR_IN_REPLY_TO_ID_A = (
    "http://schemas.microsoft.com/mapi/proptag/0x1042001E"
)
PR_IN_REPLY_TO_ID_W = (
    "http://schemas.microsoft.com/mapi/proptag/0x1042001F"
)


def internet_message_id(message):
    """Return the RFC Message-ID stored by Outlook, if available."""
    try:
        value = text(
            message.PropertyAccessor.GetProperty(
                PR_INTERNET_MESSAGE_ID
            )
        ).strip()
        if value:
            return value.lower()
    except Exception:
        pass
    return ""


def in_reply_to(message):
    """Return Outlook's In-Reply-To header/message id, if available."""
    # Outlook Object Model exposes InReplyTo on MailItem in many versions.
    for attr in ("InReplyTo",):
        try:
            value = text(
                safe_get(message, attr, "")
            ).strip()
            if value:
                return value.lower()
        except Exception:
            pass

    # Fall back to the MAPI property for clients that don't expose the OOM
    # property.
    for prop in (
        PR_IN_REPLY_TO_ID_W,
        PR_IN_REPLY_TO_ID_A,
    ):
        try:
            value = text(
                message.PropertyAccessor.GetProperty(prop)
            ).strip()
            if value:
                return value.lower()
        except Exception:
            pass

    return ""


def normalized_subject(value):
    """Normalize RE:/FW: prefixes so replies can be matched by subject."""
    subject = text(value).strip().lower()
    subject = re.sub(
        r"^(?:(?:re|fw|fwd)\s*:\s*)+",
        "",
        subject,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", subject).strip()


def _recipient_matches(recipient_values, email_address):
    target = text(email_address).strip().lower()
    if not target:
        return False

    for value in recipient_values or []:
        value = text(value).strip().lower()
        if value == target or target in extract_email_addresses(value):
            return True
    return False


def _sent_item_matches(message, sent, received):
    """Return True when *sent* is a response to the inbound *message*."""
    try:
        when = dt_utc(sent.SentOn)
    except Exception:
        return False

    if when <= received:
        return False

    # 1. Strongest signal: sent item's In-Reply-To points directly at the
    # inbound message's Internet Message-ID.
    inbound_message_id = internet_message_id(message)
    sent_in_reply_to = in_reply_to(sent)

    if inbound_message_id and sent_in_reply_to:
        if sent_in_reply_to == inbound_message_id:
            return True

    # 2. Outlook ConversationID.
    inbound_conversation = cid(message)
    sent_conversation = cid(sent)

    if (
        inbound_conversation
        and sent_conversation
        and inbound_conversation == sent_conversation
    ):
        return True

    # 3. Safe fallback: normalized subject + original sender is a recipient
    # of the sent item. This handles Outlook clients that don't preserve the
    # conversation metadata.
    inbound_subject = normalized_subject(
        safe_get(message, "Subject", "")
    )
    sent_subject = normalized_subject(
        safe_get(sent, "Subject", "")
    )

    if not inbound_subject or inbound_subject != sent_subject:
        return False

    original_sender = sender_smtp(message)
    if not original_sender:
        return False

    try:
        sent_recipients = get_recipient_details(sent)["all"]
    except Exception:
        sent_recipients = []

    return _recipient_matches(
        sent_recipients,
        original_sender,
    )


def response_diagnostic(message, response_pool, authorized_emails, engineer_pool=None):
    """Explain why a response was or was not correlated for Admin diagnostics."""
    try:
        received = dt_utc(message.ReceivedTime)
    except Exception:
        return "reply_not_found", "Inbound timestamp unavailable"
    authorized = {x.lower() for x in (authorized_emails or set()) if x}
    identified = set()
    matched_unauthorized = set()
    for candidate in list(response_pool or []) + list(engineer_pool or []):
        try:
            sender = (sender_smtp(candidate) or "").strip().lower()
            if not sender or sender.rsplit("@",1)[-1] not in INTERNAL_DOMAINS:
                continue
            when = _message_event_time(candidate)
            if not when or when <= received:
                continue
            # Detect a broad thread/header relationship first.
            related = False
            cconv, rconv = cid(message), cid(candidate)
            if cconv and rconv and cconv == rconv:
                related = True
            mid, irt = internet_message_id(message), in_reply_to(candidate)
            if mid and irt and mid == irt:
                related = True
            if not related:
                subj = normalized_subject(safe_get(message, "Subject", ""))
                rsubj = normalized_subject(safe_get(candidate, "Subject", ""))
                if subj and subj == rsubj:
                    try:
                        related = _recipient_matches(get_recipient_details(candidate)["all"], sender_smtp(message))
                    except Exception:
                        related = False
            if related:
                identified.add(sender)
                if sender not in authorized:
                    matched_unauthorized.add(sender)
        except Exception:
            continue
    if identified and matched_unauthorized:
        return "responder_not_authorized", "Responder identified but not authorized for this PDL: " + ", ".join(sorted(matched_unauthorized))[:420]
    if identified:
        return "correlation_mismatch", "Internal responder evidence exists but no qualifying authorized response was selected"
    return "reply_not_found", "No correlated internal reply found in the accessible Outlook evidence"


def first_response_detail(
    message,
    sent_items,
    valid_responder_emails=None,
):
    """Return ``(timestamp, responder_email)`` for the first valid response.

    A response is valid only when the sender is an engineer/leader explicitly
    authorized for the PDL (or a legacy engineer mapped to that PDL). This
    deliberately does not depend on the monitoring slot assignment: PDL access
    is the authority for response recognition.
    """
    try:
        received = dt_utc(message.ReceivedTime)
    except Exception:
        return None, None

    best = None
    best_sender = None
    valid_responder_emails = {
        x.lower() for x in (valid_responder_emails or set()) if x
    }

    for sent in sent_items:
        try:
            sender = (sender_smtp(sent) or "").strip().lower()
            # Empty authorization means there is no valid PDL-member responder;
            # never fall back to treating an arbitrary Sent Item as a response.
            if sender not in valid_responder_emails:
                continue
            if not _sent_item_matches(message, sent, received):
                continue

            when = dt_utc(sent.SentOn)
            if best is None or when < best:
                best = when
                best_sender = sender
        except Exception:
            continue

    return best, best_sender


def first_response(
    message,
    sent_items,
    valid_responder_emails=None,
):
    """Backward-compatible timestamp-only wrapper."""
    when, _ = first_response_detail(
        message, sent_items, valid_responder_emails
    )
    return when


def latest_response_detail(message, sent_items, valid_responder_emails=None):
    """Return the latest qualifying PDL-member reply in a thread.

    TAT continues to use the first qualifying response. This helper exists
    specifically so the UI can truthfully display the latest responded time
    when an engineer has sent more than one valid reply in the same thread.
    """
    try:
        received = dt_utc(message.ReceivedTime)
    except Exception:
        return None, None
    valid = {x.lower() for x in (valid_responder_emails or set()) if x}
    best = None
    best_sender = None
    for sent in sent_items or []:
        try:
            sender = (sender_smtp(sent) or '').strip().lower()
            if sender not in valid:
                continue
            if not _sent_item_matches(message, sent, received):
                continue
            when = dt_utc(sent.SentOn)
            if best is None or when > best:
                best = when
                best_sender = sender
        except Exception:
            continue
    return best, best_sender


def _message_event_time(message):
    """Return the best UTC timestamp for an Outlook mail item.

    Normal Sent Items expose ``SentOn`` while copies of an engineer reply
    arriving in a monitored PDL/shared mailbox expose ``ReceivedTime``.
    Supporting both lets mapped engineers remain excluded from monitoring
    while their actual replies still satisfy response/TAT tracking.
    """
    for attr in ("SentOn", "ReceivedTime"):
        try:
            value = safe_get(message, attr, None)
            if value:
                return dt_utc(value)
        except Exception:
            continue
    return None


def _engineer_reply_matches(inbound, engineer_message, inbound_received):
    """Return True when a mapped engineer message replies to *inbound*.

    Engineer-originated messages can be visible as inbound copies in a PDL
    mailbox rather than in the local user's Sent Items.  They are therefore
    used only as response evidence and are never imported as monitored rows.
    """
    when = _message_event_time(engineer_message)
    if when is None or when <= inbound_received:
        return False

    # Strongest signal available in Outlook: same conversation.
    inbound_conversation = cid(inbound)
    reply_conversation = cid(engineer_message)
    if (
        inbound_conversation
        and reply_conversation
        and inbound_conversation == reply_conversation
    ):
        return True

    # Header-level reply linkage, when Outlook preserved it on the PDL copy.
    inbound_message_id = internet_message_id(inbound)
    reply_in_reply_to = in_reply_to(engineer_message)
    if inbound_message_id and reply_in_reply_to:
        if inbound_message_id == reply_in_reply_to:
            return True

    # Conservative fallback: same normalized subject AND the original customer
    # is explicitly a recipient of the engineer message.  This prevents an
    # unrelated internal mail with a similar subject from closing the TAT.
    inbound_subject = normalized_subject(safe_get(inbound, "Subject", ""))
    reply_subject = normalized_subject(safe_get(engineer_message, "Subject", ""))
    if not inbound_subject or inbound_subject != reply_subject:
        return False

    original_sender = sender_smtp(inbound)
    if not original_sender:
        return False

    try:
        reply_recipients = get_recipient_details(engineer_message)["all"]
    except Exception:
        reply_recipients = []

    return _recipient_matches(reply_recipients, original_sender)


def first_engineer_response_detail(message, engineer_messages):
    """Return ``(timestamp, responder_email)`` for the first PDL-member reply."""
    try:
        received = dt_utc(message.ReceivedTime)
    except Exception:
        return None, None

    best = None
    best_sender = None
    for candidate in engineer_messages or []:
        try:
            if not _engineer_reply_matches(message, candidate, received):
                continue
            when = _message_event_time(candidate)
            if when is not None and (best is None or when < best):
                best = when
                best_sender = (sender_smtp(candidate) or "").strip().lower() or None
        except Exception:
            continue
    return best, best_sender


def first_engineer_response(message, engineer_messages):
    """Backward-compatible timestamp-only wrapper."""
    when, _ = first_engineer_response_detail(message, engineer_messages)
    return when


def latest_engineer_response_detail(message, engineer_messages):
    """Return the latest matching engineer response in a monitored thread."""
    try:
        received = dt_utc(message.ReceivedTime)
    except Exception:
        return None, None
    best = None
    best_sender = None
    for candidate in engineer_messages or []:
        try:
            if not _engineer_reply_matches(message, candidate, received):
                continue
            when = _message_event_time(candidate)
            if when is not None and (best is None or when > best):
                best = when
                best_sender = (sender_smtp(candidate) or '').strip().lower() or None
        except Exception:
            continue
    return best, best_sender


# ============================================================
# PDL MAIL SOURCE DISCOVERY
# ============================================================

def _folder_identity(folder):
    """Best-effort stable identity for an Outlook folder."""
    return (
        text(safe_get(folder, "EntryID", "")).strip(),
        text(safe_get(folder, "FolderPath", "")).strip().lower(),
    )


def _resolve_shared_folder(namespace, address, folder_type):
    """Resolve a PDL/shared mailbox default folder without Entra/API access.

    Distribution lists normally resolve but do not expose mailbox folders; in
    that case this returns None and recipient-based matching remains active.
    """
    address = text(address).strip()
    if not address:
        return None
    try:
        recipient = namespace.CreateRecipient(address)
        recipient.Resolve()
        if not bool(safe_get(recipient, "Resolved", False)):
            return None
        return namespace.GetSharedDefaultFolder(recipient, folder_type)
    except Exception:
        return None


def discover_pdl_mail_sources(namespace, primary_store, pdls):
    """Discover where each configured PDL is actually visible in Outlook.

    The default/personal Inbox is always retained for recipient matching. For
    each configured PDL we then attempt delegated/shared-mailbox discovery via
    MAPI and also inspect separately mounted Outlook stores whose display name
    matches the PDL name/address. No Microsoft Graph/Entra admin dependency is
    introduced.
    """
    sources = []
    status = {}
    seen = set()

    if graph_enabled():
        primary_inbox = get_inbox(namespace, primary_store)
        sources.append({
            "folder": primary_inbox,
            "pdl": None,
            "mode": "graph_dedicated_mailbox",
            "label": graph_config().get("mailbox", "").strip(),
        })
        for pdl in pdls:
            status[pdl.name] = {
                "pdl": pdl.name,
                "address": pdl.address,
                "mode": "graph_dedicated_mailbox",
                "source": graph_config().get("mailbox", "").strip(),
                "accessible": True,
            }
        return sources, status

    primary_inbox = get_inbox(namespace, primary_store)
    key = _folder_identity(primary_inbox)
    seen.add(key)
    sources.append({
        "folder": primary_inbox,
        "pdl": None,
        "mode": "primary_recipient_match",
        "label": text(safe_get(primary_store, "DisplayName", "Primary Outlook mailbox")),
    })

    stores = get_all_stores(namespace)

    for pdl in pdls:
        pdl_status = {
            "pdl": pdl.name,
            "address": pdl.address,
            "mode": "recipient_match",
            "source": text(safe_get(primary_store, "DisplayName", "Primary Outlook mailbox")),
            "accessible": True,
        }

        # Preferred path for a delegated/shared mailbox.
        shared_inbox = _resolve_shared_folder(namespace, pdl.address, OL_FOLDER_INBOX)
        if shared_inbox is not None:
            skey = _folder_identity(shared_inbox)
            if skey not in seen:
                seen.add(skey)
                sources.append({
                    "folder": shared_inbox,
                    "pdl": pdl,
                    "mode": "shared_mailbox",
                    "label": text(safe_get(shared_inbox, "FolderPath", "")) or pdl.address,
                })
            pdl_status.update({
                "mode": "shared_mailbox",
                "source": text(safe_get(shared_inbox, "FolderPath", "")) or pdl.address,
                "accessible": True,
            })
            status[pdl.name] = pdl_status
            continue

        # Some shared/delegated mailboxes are mounted as independent stores.
        pdl_name = normalize_name(pdl.name)
        pdl_address = normalize_name(pdl.address)
        localpart = pdl_address.split("@", 1)[0] if "@" in pdl_address else pdl_address
        matched_store = None
        for store in stores:
            if store is primary_store:
                continue
            display = normalize_name(safe_get(store, "DisplayName", ""))
            if not display:
                continue
            if (pdl_address and pdl_address in display) or (localpart and localpart in display) or (pdl_name and pdl_name in display):
                matched_store = store
                break
        if matched_store is not None:
            try:
                mounted_inbox = get_inbox(namespace, matched_store)
                mkey = _folder_identity(mounted_inbox)
                if mkey not in seen:
                    seen.add(mkey)
                    sources.append({
                        "folder": mounted_inbox,
                        "pdl": pdl,
                        "mode": "mounted_store",
                        "label": text(safe_get(matched_store, "DisplayName", pdl.name)),
                    })
                pdl_status.update({
                    "mode": "mounted_store",
                    "source": text(safe_get(matched_store, "DisplayName", pdl.name)),
                    "accessible": True,
                })
            except Exception:
                pass

        status[pdl.name] = pdl_status

    return sources, status


def discover_pdl_sent_sources(namespace, primary_sent, pdls):
    """Return default plus accessible shared Sent Items keyed by PDL id."""
    default_items = []
    shared_folders = {}
    for pdl in pdls:
        folder = _resolve_shared_folder(namespace, pdl.address, OL_FOLDER_SENT)
        if folder is not None:
            shared_folders[pdl.id] = folder
    return default_items, shared_folders

def discover_engineer_sent_items(namespace, engineers, cutoff):
    if graph_enabled():
        # The dedicated mailbox's Sent Items are already included in the main
        # response pool. App-only Graph does not require opening engineers'
        # personal stores, and doing so would defeat least-privilege mailbox
        # scoping.
        return []

    """Collect recent Sent Items from Outlook stores belonging to configured engineers.

    A PDL response must be recognized even when the engineer sends from their own
    mailbox and the reply is not copied into the monitored PDL mailbox.  Outlook
    profiles can expose multiple mailbox stores, so we inspect accessible stores
    whose display/root name matches a configured engineer.  This is response
    evidence only; these messages are never imported as workload rows.
    """
    engineer_emails = {
        (e.email or '').strip().lower() for e in engineers if getattr(e, 'email', None)
    }
    engineer_names = {
        normalize_name(e.name) for e in engineers if getattr(e, 'name', None)
    }
    engineer_localparts = {
        email.split('@', 1)[0] for email in engineer_emails if '@' in email
    }

    collected = []
    seen = set()
    stores = get_all_stores(namespace)

    for store in stores:
        try:
            display = normalize_name(safe_get(store, 'DisplayName', ''))
            root = store.GetRootFolder()
            root_name = normalize_name(safe_get(root, 'Name', ''))
            candidates = ' '.join(x for x in (display, root_name) if x)

            # Do not inspect arbitrary stores.  A store must identify itself by
            # engineer name, mailbox local-part, or full SMTP address.
            matched = False
            for email in engineer_emails:
                if email and email in candidates:
                    matched = True
                    break
            if not matched:
                for localpart in engineer_localparts:
                    if localpart and localpart in candidates:
                        matched = True
                        break
            if not matched:
                for name in engineer_names:
                    if name and name in candidates:
                        matched = True
                        break
            if not matched:
                continue

            folder = get_sent_items(namespace, store)
            for item in recent_sent_items(folder, cutoff):
                try:
                    entry_id = text(safe_get(item, 'EntryID', '')).strip()
                    key = entry_id or (internet_message_id(item), text(safe_get(item, 'SentOn', '')))
                    if key in seen:
                        continue
                    seen.add(key)
                    sender = (sender_smtp(item) or '').lower()
                    if sender in engineer_emails:
                        collected.append(item)
                except Exception:
                    continue
        except Exception:
            continue

    return collected


def recent_sent_items(folder, cutoff):
    """Return recent MailItems from a Sent Items folder using SentOn."""
    output = []
    try:
        items = folder.Items
        try:
            items.Sort('[SentOn]', True)
        except Exception:
            pass
        for item in items:
            try:
                if safe_get(item, 'Class', None) != OL_MAIL_ITEM:
                    continue
                sent_value = safe_get(item, 'SentOn', None)
                if not sent_value:
                    continue
                sent_at = dt_utc(sent_value)
                if sent_at < cutoff:
                    break
                output.append(item)
            except Exception:
                continue
    except Exception:
        return []
    return output


# ============================================================
# DATABASE RESPONSE RECONCILIATION
# ============================================================

def reconcile_response_state_from_database(pdls, cutoff):
    """Reconcile customer response state from the central mailbox database.

    This is the final response authority after Outlook object matching. It is
    deliberately thread-centric: a PDL-member engineer message can prove a
    response through ConversationID, reply headers (when available through the
    stored metadata), or normalized subject + recipient evidence. This also
    repairs rows when an engineer's personal Sent Items are not mounted in the
    current Outlook profile but the central mailbox received the reply copy.
    """
    changed = 0
    try:
        engineers = {e.email.strip().lower(): e for e in Engineer.query.filter_by(active=True).all() if e.email}
        pdl_map = {p.id: p for p in pdls}
        recent = Email.query.filter(Email.received_at >= cutoff).order_by(Email.received_at.asc()).all()
        by_pdl = {}
        for row in recent:
            if row.pdl_id in pdl_map:
                by_pdl.setdefault(row.pdl_id, []).append(row)

        for pdl_id, rows in by_pdl.items():
            pdl = pdl_map[pdl_id]
            responders = {
                email: engineer for email, engineer in engineers.items()
                if engineer_has_pdl_response_authority(engineer, pdl)
            }
            if not responders:
                continue
            engineer_rows = [r for r in rows if (r.sender_email or '').strip().lower() in responders and r.sender_type == 'engineer']
            if not engineer_rows:
                continue
            for customer in rows:
                if (customer.sender_type or '').lower() != 'customer':
                    continue
                customer_sender = (customer.sender_email or '').strip().lower()
                if not customer_sender:
                    continue
                candidates=[]
                c_conv=(customer.conversation_id or '').strip()
                c_subject=normalized_subject(customer.subject or '')
                for reply in engineer_rows:
                    if not reply.received_at or reply.received_at <= customer.received_at:
                        continue
                    r_conv=(reply.conversation_id or '').strip()
                    matched=False
                    if c_conv and r_conv and c_conv == r_conv:
                        matched=True
                    elif c_subject and c_subject == normalized_subject(reply.subject or ''):
                        recipients=((reply.recipients or '') + ' ' + (reply.cc or '')).lower()
                        # A subject match is only valid when the engineer actually
                        # addressed the original customer; this prevents unrelated
                        # same-subject internal messages from closing TAT.
                        matched=customer_sender in recipients
                    if matched:
                        candidates.append(reply)
                if not candidates:
                    continue
                candidates.sort(key=lambda r: r.received_at)
                first=candidates[0]; last=candidates[-1]
                engineer=responders.get((first.sender_email or '').strip().lower())
                last_engineer=responders.get((last.sender_email or '').strip().lower())
                if not customer.responded or not customer.first_response_at or first.received_at < customer.first_response_at:
                    customer.responded=True
                    customer.first_response_at=first.received_at
                    customer.response_minutes=round((first.received_at-customer.received_at).total_seconds()/60,2)
                    customer.response_source='database_thread_reconcile'
                    customer.response_diagnostic='successfully_responded'
                    customer.response_match_detail=f"Database thread reconciliation matched {first.sender_email}"
                    customer.response_engineer_email=first.sender_email
                    customer.response_engineer_name=engineer.name if engineer else None
                    changed += 1
                if not customer.last_response_at or last.received_at > customer.last_response_at:
                    customer.last_response_at=last.received_at
                    customer.last_response_engineer_email=last.sender_email
                    customer.last_response_engineer_name=last_engineer.name if last_engineer else None
                    changed += 1
                if customer.tat_minutes is not None and customer.response_minutes is not None:
                    customer.tat_breached = customer.response_minutes > customer.tat_minutes
        if changed:
            db.session.commit()
    except Exception as exc:
        try: db.session.rollback()
        except Exception: pass
        log_event('WARN','response_reconcile','Database response reconciliation failed.',str(exc))
    return changed

# ============================================================
# SYNC
# ============================================================


def _build_pdl_responder_cache(pdls, engineers):
    """Build PDL -> authorized responder email map once per sync.

    This is deliberately independent of contact_role. Team Management/PDL access
    remains the authoritative permission, matching the response-correlation rules.
    """
    cache = {}
    for pdl in pdls:
        allowed = {}
        for engineer in engineers:
            email = (getattr(engineer, "email", "") or "").strip().lower()
            if not email:
                continue
            try:
                if engineer_has_pdl_response_authority(engineer, pdl):
                    allowed[email] = engineer
            except Exception:
                continue
        cache[pdl.id] = allowed
    return cache



def _pdl_discovery_state_key(pdl_id):
    return f"pdl_member_discovery_done:{int(pdl_id)}"


def _pdl_needs_initial_member_discovery(pdl):
    """Return True only until the PDL's initial member discovery succeeds.

    Discovery is intentionally persisted per PDL rather than per sync. This keeps
    normal/background syncs fast while allowing a newly-added PDL to discover its
    members on its first successful sync. A failed discovery is retried on the
    next sync.
    """
    try:
        return not bool(get_sync_state(_pdl_discovery_state_key(pdl.id)))
    except Exception:
        # If the state store is temporarily unavailable, prefer correctness:
        # discover rather than silently losing responder membership.
        return True


def _mark_pdl_member_discovery_complete(pdl):
    set_sync_state(
        _pdl_discovery_state_key(pdl.id),
        datetime.now(timezone.utc).isoformat(),
    )


def _discover_initial_members_only(pdls, namespace):
    """Discover members only for PDLs whose initial discovery is pending."""
    pending = [p for p in pdls if _pdl_needs_initial_member_discovery(p)]
    if not pending:
        return {}, []

    discovered_by_pdl = {}
    completed = []
    for pdl in pending:
        try:
            ensure_pdl_team_group(pdl)
            found = []
            for email, name in discover_pdl_members_from_outlook(namespace, pdl):
                _, changed = _upsert_discovered_member(
                    pdl, email, name, "outlook_membership"
                )
                if changed:
                    found.append(email)
            # Reaching this point means the Outlook membership query completed.
            # An empty membership result is still a successful discovery; otherwise
            # a PDL with no members would trigger an expensive discovery forever.
            _mark_pdl_member_discovery_complete(pdl)
            completed.append(pdl.id)
            discovered_by_pdl[pdl.id] = sorted(set(found))
        except Exception as exc:
            discovered_by_pdl[pdl.id] = []
            try:
                log_event(
                    "WARN",
                    "pdl_member_discovery",
                    f"Initial PDL member discovery failed for {pdl.name}; it will be retried on the next sync.",
                    str(exc),
                )
            except Exception:
                pass

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    return discovered_by_pdl, completed


def _discover_members_once_per_pdl(pdls, namespace, response_pools_by_pdl=None):
    """Perform automatic membership discovery once per PDL per sync.

    Outlook DL/group expansion is expensive. The old implementation called
    auto_discover_pdl_members() for every customer message, causing repeated
    MAPI recipient expansion and database queries. We now do it once per PDL.
    Verified-reply discovery is also done once against the PDL response pool.
    """
    discovered_by_pdl = {}
    for pdl in pdls:
        try:
            pool = (response_pools_by_pdl or {}).get(pdl.id, [])
            # Pass a single representative message only when there is a pool;
            # verified reply discovery requires a customer message and therefore
            # is intentionally handled by the normal correlation path below.
            ensure_pdl_team_group(pdl)
            found = []
            for email, name in discover_pdl_members_from_outlook(namespace, pdl):
                _, changed = _upsert_discovered_member(pdl, email, name, "outlook_membership")
                if changed:
                    found.append(email)
            discovered_by_pdl[pdl.id] = sorted(set(found))
        except Exception as exc:
            discovered_by_pdl[pdl.id] = []
            try:
                log_event(
                    "WARN",
                    "pdl_member_discovery",
                    f"PDL member discovery failed for {pdl.name}; manual mapping remains available.",
                    str(exc),
                )
            except Exception:
                pass
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    return discovered_by_pdl


def _sync_timing(label, started, timings):
    elapsed = round(time.perf_counter() - started, 3)
    timings[label] = elapsed
    return elapsed


def sync_outlook(force_full=False):

    # Graph app-only synchronization does not require Outlook/COM.  Keep COM
    # initialization only for the legacy Outlook fallback.
    com_initialized = False
    if not graph_enabled():
        if pythoncom is None:
            raise RuntimeError(
                "Microsoft Graph mailbox is not configured. Open Settings > Microsoft Graph Mailbox."
            )
        pythoncom.CoInitialize()
        com_initialized = True

    try:

        pdls = list_pdls()

        if not pdls:

            raise RuntimeError(
                "No active PDLs configured."
            )

        # ----------------------------------------------------
        # SYNC PERFORMANCE CONTEXT
        # ----------------------------------------------------
        sync_started = time.perf_counter()
        timings = {}

        # Load active engineers exactly once. The previous build repeatedly hit
        # Engineer.query inside the per-email loop.
        active_engineers = list(Engineer.query.filter_by(active=True).all())
        engineer_contacts = {
            (e.email or "").strip().lower(): e
            for e in active_engineers
            if getattr(e, "email", None)
        }
        responder_engineers = dict(engineer_contacts)
        authorized_pdl_responders_by_pdl = _build_pdl_responder_cache(pdls, active_engineers)

        # ----------------------------------------------------
        # OUTLOOK
        # ----------------------------------------------------

        set_sync_state("sync_progress", "Connecting to mailbox")
        log_event("INFO", "outlook_sync_progress", "SYNC: Graph mailbox connection established/starting mailbox discovery.")
        _, namespace = connect_outlook()

        store = get_store(
            namespace
        )

        store_name = text(
            safe_get(
                store,
                "DisplayName",
                "",
            )
        )

        if is_archive(store_name):

            raise RuntimeError(
                "Safety check: Online Archive "
                "selected; sync aborted."
            )

        inbox = get_inbox(
            namespace,
            store,
        )

        sent = get_sent_items(
            namespace,
            store,
        )

        # Keep the personal/default mailbox behavior intact, then augment it
        # with any live PDL shared/delegated mailboxes that Outlook exposes.
        mail_sources, pdl_source_status = discover_pdl_mail_sources(
            namespace, store, pdls
        )

        inbox_name = text(
            safe_get(
                inbox,
                "Name",
                "",
            )
        )

        sent_name = text(
            safe_get(
                sent,
                "Name",
                "",
            )
        )

        # ----------------------------------------------------
        # DATE RANGE
        # ----------------------------------------------------

        now_utc = datetime.now(timezone.utc)
        # Incremental sync uses a rolling overlap so messages missed during a
        # transient Outlook/COM/SQLite issue are recovered on the next run.
        # Manual Sync Outlook performs a full SYNC_DAYS scan by default so the
        # user can immediately recover anything missed by an earlier run.
        cutoff = now_utc - timedelta(days=SYNC_DAYS)
        last_sync_raw = get_sync_state("last_sync")
        if last_sync_raw and not force_full:
            try:
                last_sync = datetime.fromisoformat(last_sync_raw.replace("Z", "+00:00"))
                if last_sync.tzinfo is None:
                    last_sync = last_sync.replace(tzinfo=timezone.utc)
                incremental_cutoff = last_sync - timedelta(minutes=max(15, SYNC_OVERLAP_MINUTES))
                if incremental_cutoff > cutoff:
                    cutoff = incremental_cutoff
            except (TypeError, ValueError):
                pass

        # Ensure every configured PDL has a canonical Team Management group.
        # Membership discovery itself is performed once per PDL below, not once
        # for every email.
        for configured_pdl in pdls:
            try:
                ensure_pdl_team_group(configured_pdl)
            except Exception:
                db.session.rollback()
        db.session.commit()

        set_sync_state("sync_progress", "Scanning mailbox folders")
        log_event("INFO", "outlook_sync_progress", "SYNC: scanning configured Graph mailbox sources.")
        inbox_candidates = []
        folder_stats = []
        source_candidate_counts = {}

        # Divide the configured scan budget across discovered sources while
        # ensuring every live PDL source gets a meaningful window.
        per_source_limit = max(100, int(SYNC_LIMIT / max(1, len(mail_sources))))
        for source in mail_sources:
            source_items, source_stats = collect_recent_inbox_items(
                source["folder"],
                cutoff,
                per_source_limit,
            )
            label = source["label"] or source["mode"]
            source_candidate_counts[label] = len(source_items)
            for received, folder_path, message in source_items:
                inbox_candidates.append((received, folder_path, message, source.get("pdl")))
            for stat in source_stats:
                enriched = dict(stat) if isinstance(stat, dict) else {"detail": stat}
                enriched["source"] = label
                enriched["mode"] = source["mode"]
                enriched["pdl"] = source["pdl"].name if source.get("pdl") else None
                folder_stats.append(enriched)

        inbox_candidates.sort(key=lambda row: row[0], reverse=True)

        # Keep a diagnostic event/result even when Outlook returns zero items.
        # This makes the distinction between "Outlook has no recent mail" and
        # "PDL matching/import failed" visible in Administration > Events.
        if not inbox_candidates:
            log_event(
                "WARN",
                "outlook_sync",
                "Mailbox sync found no mail candidates in the configured scan window.",
                json.dumps({
                    "cutoff": cutoff.isoformat(),
                    "sources": source_candidate_counts,
                    "source_status": pdl_source_status,
                    "force_full": bool(force_full),
                }, default=str),
            )

        set_sync_state("sync_progress", f"Collected {len(inbox_candidates)} inbox candidate(s); preparing response correlation")
        log_event("INFO", "outlook_sync_progress", f"SYNC: collected {len(inbox_candidates)} inbox candidate(s); preparing response evidence.")
        # Response detection checks both the normal Sent Items and any shared
        # PDL Sent Items that are accessible in the current Outlook profile.
        sent_items = recent_sent_items(sent, cutoff)
        # Important: engineer replies may be sent from the engineer's own
        # Outlook mailbox rather than the monitored PDL/shared mailbox.  Keep
        # those Sent Items as response evidence only.
        engineer_sent_items = discover_engineer_sent_items(
            namespace,
            active_engineers,
            cutoff,
        )
        sent_by_pdl = {}
        for pdl in pdls:
            shared_sent = _resolve_shared_folder(namespace, pdl.address, OL_FOLDER_SENT)
            if shared_sent is not None:
                try:
                    sent_by_pdl[pdl.id] = recent_items(shared_sent, cutoff)
                except Exception:
                    sent_by_pdl[pdl.id] = []

        # ----------------------------------------------------
        # ENGINEERS
        # ----------------------------------------------------

        # active_engineers / engineer_contacts were loaded once at sync start.

        # PDL response authority is based on explicit PDL access, not on the
        # monitoring slot and not on contact_role. A manager/leadership member
        # who is explicitly granted access to a PDL is also a valid responder.
        # Only the operational role "engineer" is excluded from workload when
        # their own outbound message is encountered; leadership mail remains
        # visible for intelligence/notification purposes.
        # Every engineer explicitly authorized for a PDL is both a valid responder
        # and a workload-exempt sender for that PDL. Do not use contact_role here: a
        # manager/lead who is also a PDL member must not have their customer reply
        # re-imported as a new escalation/work item.
        authorized_pdl_responders = engineer_contacts
        engineers = engineer_contacts
        # Action-required copy detection includes explicitly configured
        # executives plus manager/leadership designations, so a manager in CC
        # is actionable even when the admin has not toggled "executive".
        leadership_terms = (
            "manager", "director", "head", "lead", "leadership",
            "vice president", "vp", "chief", "executive", "gm",
        )
        executives = {}
        for e in active_engineers:
            designation = (e.designation or "").strip().lower()
            role = (getattr(e, "contact_role", "engineer") or "engineer").strip().lower()
            if e.is_executive or role in {"manager", "executive"} or any(term in designation for term in leadership_terms):
                executives[e.email.lower()] = e

        # ----------------------------------------------------
        # MAPPED ENGINEER RESPONSE POOL
        # ----------------------------------------------------
        # Messages *from* addresses configured under Map Engineers are not
        # monitored/imported.  Before filtering them out, retain the Outlook
        # objects transiently so they can prove that an earlier customer email
        # was answered.  Pools are separated by PDL to avoid cross-PDL matches.
        engineer_responses_by_pdl = {}
        for candidate_received, candidate_folder, candidate_message, candidate_source_pdl in inbox_candidates:
            try:
                candidate_sender = (sender_smtp(candidate_message) or "").strip().lower()
                engineer_contact = authorized_pdl_responders.get(candidate_sender)
                if engineer_contact is None:
                    continue
                candidate_pdl = candidate_source_pdl or find_pdl(candidate_message, pdls)
                if candidate_pdl is None:
                    continue
                # Team Management PDL access is the authoritative responder
                # permission. A group membership without explicit PDL access
                # must never close the customer's response/TAT.
                if not engineer_has_pdl_response_authority(engineer_contact, candidate_pdl):
                    continue
                engineer_responses_by_pdl.setdefault(candidate_pdl.id, []).append(candidate_message)
            except Exception:
                continue

        # ----------------------------------------------------
        # INITIAL PDL MEMBER DISCOVERY -- ONCE PER PDL, NOT ONCE PER SYNC
        # ----------------------------------------------------
        # A newly-created PDL has no discovery-state key, so its first successful
        # sync discovers the Outlook DL membership and creates/populates the
        # canonical "PDL - <PDL Name>" Team Management group. Subsequent syncs do
        # NOT perform Outlook DL expansion. PDL owners can add members manually;
        # those members use the same response-correlation path immediately.
        discovered_members_by_pdl = {}
        discovery_pending_pdl_ids = []
        if SYNC_MEMBER_DISCOVERY_ONCE:
            discovery_started = time.perf_counter()
            discovered_members_by_pdl, discovery_pending_pdl_ids = (
                _discover_initial_members_only(pdls, namespace)
            )
            _sync_timing("member_discovery_initial_only", discovery_started, timings)

            if discovery_pending_pdl_ids:
                # Rebuild authority after discovery because new Engineer rows/access
                # may have been created during this same sync.
                active_engineers = list(Engineer.query.filter_by(active=True).all())
                engineer_contacts = {
                    (e.email or "").strip().lower(): e
                    for e in active_engineers
                    if getattr(e, "email", None)
                }
                authorized_pdl_responders_by_pdl = _build_pdl_responder_cache(
                    pdls, active_engineers
                )
                responder_engineers.update(engineer_contacts)

                # IMPORTANT: discovered responders may have their reply copy in the
                # monitored PDL/Inbox. The response pool was built before discovery,
                # so rebuild it after newly-discovered authority is available.
                engineer_responses_by_pdl = {}
                for candidate_received, candidate_folder, candidate_message, candidate_source_pdl in inbox_candidates:
                    try:
                        candidate_sender = (sender_smtp(candidate_message) or "").strip().lower()
                        engineer_contact = engineer_contacts.get(candidate_sender)
                        if engineer_contact is None:
                            continue
                        candidate_pdl = candidate_source_pdl or find_pdl(candidate_message, pdls)
                        if candidate_pdl is None:
                            continue
                        if not engineer_has_pdl_response_authority(engineer_contact, candidate_pdl):
                            continue
                        engineer_responses_by_pdl.setdefault(candidate_pdl.id, []).append(candidate_message)
                    except Exception:
                        continue

        # Hide stale engineer outbound copies that were imported by older versions.
        # Keep the rows for audit/history and response evidence, but remove them from
        # all operational email lists and notifications.
        try:
            for pdl in pdls:
                authorized_emails = {
                    e.email.strip().lower() for e in engineer_contacts.values()
                    if e.email and engineer_has_pdl_response_authority(e, pdl)
                }
                if authorized_emails:
                    stale = Email.query.filter(
                        Email.pdl_id == pdl.id,
                        func.lower(Email.sender_email).in_(authorized_emails),
                    ).all()
                    for stale_email in stale:
                        stale_email.monitoring_excluded = True
                        stale_email.response_required = False
                        stale_email.engineer_action_required = False
                        stale_email.customer_waiting = False
                        Alert.query.filter(Alert.email_id == stale_email.id).update(
                            {Alert.read: True}, synchronize_session=False
                        )
            db.session.commit()
        except Exception:
            db.session.rollback()

        imported = 0
        matched = 0
        duplicates_ignored = 0
        automatic_ignored = 0
        engineer_ignored = 0

        errors = []

        matched_by_pdl = {}
        imported_by_folder = {}

        # ----------------------------------------------------
        # PROCESS EMAILS
        # ----------------------------------------------------

        for index, (
            received,
            folder_path,
            message,
            source_pdl,
        ) in enumerate(
            inbox_candidates,
            start=1,
        ):

            pdl = None
            try:

                if is_internal_tool_message(message):
                    automatic_ignored += 1
                    continue

                if is_automatic_message(message):
                    automatic_ignored += 1
                    continue

                # A dedicated shared/mounted mailbox is itself authoritative
                # for PDL ownership. The personal/default Inbox still uses the
                # existing recipient/name matching logic.
                pdl = source_pdl or find_pdl(
                    message,
                    pdls,
                )

                if not pdl:
                    continue

                matched += 1

                matched_by_pdl[
                    pdl.name
                ] = (
                    matched_by_pdl.get(
                        pdl.name,
                        0,
                    )
                    + 1
                )

                # A mapped engineer's message may appear only as a copy in the
                # central/shared PDL mailbox, not in this user's Sent Items.
                # Evaluate both sources and keep the earliest valid response.
                response_pool = list(sent_items)
                response_pool.extend(engineer_sent_items)
                if pdl.id in sent_by_pdl:
                    response_pool.extend(sent_by_pdl[pdl.id])

                # Use the responder cache built once per PDL. This is the key
                # performance fix: no Outlook membership expansion and no Engineer
                # table scan occurs inside the per-email loop.
                authorized_pdl_responders = authorized_pdl_responders_by_pdl.get(
                    pdl.id, {}
                )
                valid_pdl_responder_emails = set(authorized_pdl_responders.keys())
                responder_engineers.update(authorized_pdl_responders)

                # Extract the base message fields BEFORE any intelligence/TAT
                # calculation.  In V10.20.15, intel was first referenced by the
                # TAT calculation and only assigned later, causing every matched
                # message to fail with UnboundLocalError and leaving imported=0.
                sender_email = sender_smtp(message)
                sender_email_lower = sender_email.lower()
                sender_domain = sender_email.rsplit("@", 1)[-1].lower() if "@" in sender_email else ""
                sender_type = (
                    "engineer"
                    if sender_email_lower in responder_engineers
                    else (
                        "internal"
                        if sender_domain in INTERNAL_DOMAINS
                        else (
                            "customer"
                            if "@" in sender_email
                            else "unknown"
                        )
                    )
                )

                subject = text(safe_get(message, "Subject", ""))
                body = text(safe_get(message, "Body", ""))
                info = get_recipient_details(message)
                internet_id = internet_message_id(message)

                if is_duplicate_message(message, internet_id):
                    duplicates_ignored += 1
                    continue

                executive_hits = [
                    executives[address]
                    for address in info["all"]
                    if address in executives
                ]
                executive_sender = sender_email_lower in executives
                executive_context = bool(executive_hits or executive_sender)

                sender_engineer = responder_engineers.get(sender_email_lower)
                configured_engineer_sender = bool(sender_engineer)
                pdl_member_sender = bool(
                    sender_engineer
                    and engineer_has_pdl_response_authority(sender_engineer, pdl)
                )

                sent_response, sent_responder = first_response_detail(
                    message,
                    response_pool,
                    valid_pdl_responder_emails,
                )
                mapped_engineer_response, mapped_responder = first_engineer_response_detail(
                    message,
                    engineer_responses_by_pdl.get(pdl.id, []),
                )

                response_candidates = [
                    (value, responder)
                    for value, responder in (
                        (sent_response, sent_responder),
                        (mapped_engineer_response, mapped_responder),
                    )
                    if value is not None
                ]
                response, response_engineer_email = (
                    min(response_candidates, key=lambda item: item[0])
                    if response_candidates else (None, None)
                )
                response_engineer = authorized_pdl_responders.get(
                    (response_engineer_email or "").lower()
                )

                latest_response, latest_response_engineer_email = latest_response_detail(
                    message, response_pool, valid_pdl_responder_emails
                )
                mapped_latest_response, mapped_latest_responder = latest_engineer_response_detail(
                    message, engineer_responses_by_pdl.get(pdl.id, []),
                )
                latest_candidates = [
                    (value, responder)
                    for value, responder in (
                        (latest_response, latest_response_engineer_email),
                        (mapped_latest_response, mapped_latest_responder),
                    )
                    if value is not None
                ]
                if latest_candidates:
                    latest_response, latest_response_engineer_email = max(
                        latest_candidates, key=lambda item: item[0]
                    )
                else:
                    latest_response, latest_response_engineer_email = None, None
                latest_response_engineer = authorized_pdl_responders.get(
                    (latest_response_engineer_email or "").lower()
                )

                response_minutes = (
                    round((response - received).total_seconds() / 60, 2)
                    if response else None
                )

                # Build intelligence BEFORE using it.  A first pass without TAT
                # is used only to establish monitoring/category state.  This keeps
                # recognition and engineer messages out of TAT while avoiding a
                # second full classification unless TAT actually changes.
                intel = build_intelligence(
                    subject,
                    body,
                    sender_email,
                    sender_type,
                    tat_breached=False,
                    response_minutes=response_minutes,
                    tat_minutes=pdl.tat_minutes,
                    recipient_count=len(info["all"]),
                    executive_recipient_count=len(executive_hits),
                    executive_designations=[
                        e.designation or e.name for e in executive_hits
                    ],
                    executive_context=executive_context,
                )

                elapsed_minutes = round(
                    (datetime.now(timezone.utc) - received).total_seconds() / 60, 2
                )
                tat_breached = bool(
                    not intel.get("monitoring_excluded")
                    and str(intel.get("email_category") or "").lower() != "recognition"
                    and pdl.tat_minutes is not None
                    and (
                        (response_minutes is not None and response_minutes > pdl.tat_minutes)
                        or (response_minutes is None and elapsed_minutes > pdl.tat_minutes)
                    )
                )

                # Rebuild only when TAT changes the intelligence result.
                if tat_breached:
                    intel = build_intelligence(
                        subject,
                        body,
                        sender_email,
                        sender_type,
                        tat_breached=True,
                        response_minutes=response_minutes,
                        tat_minutes=pdl.tat_minutes,
                        recipient_count=len(info["all"]),
                        executive_recipient_count=len(executive_hits),
                        executive_designations=[
                            e.designation or e.name for e in executive_hits
                        ],
                        executive_context=executive_context,
                    )

                response_diag, response_diag_detail = response_diagnostic(
                    message, response_pool, valid_pdl_responder_emails,
                    engineer_responses_by_pdl.get(pdl.id, []),
                )
                if response_engineer_email:
                    response_diag = "successfully_responded"
                    response_diag_detail = f"Authorized PDL responder: {response_engineer_email}"

                # Configured engineers are workload-exempt. PDL membership is
                # separately used to authorize their outbound message as response
                # evidence.
                if configured_engineer_sender:
                    intel["response_required"] = False
                    intel["monitoring_excluded"] = True
                    intel["exclusion_reason"] = (
                        "Configured engineer / team-member communication; operational event only"
                    )
                    intel["customer_waiting"] = False
                    intel["engineer_action_required"] = False

                engineer = (
                    engineers.get(sender_email_lower)
                    if sender_type == "engineer"
                    else None
                )

                data = {

                    "entry_id": text(
                        safe_get(
                            message,
                            "EntryID",
                            "",
                        )
                    ),

                    "internet_message_id":
                        internet_id,

                    "conversation_id": cid(
                        message
                    ),

                    "subject": subject,

                    "sender_name": text(
                        safe_get(
                            message,
                            "SenderName",
                            "",
                        )
                    ),

                    "sender_email":
                        sender_email,

                    "sender_domain":
                        sender_domain,

                    "recipients":
                        recipients(message),

                    "cc": text(
                        safe_get(
                            message,
                            "CC",
                            "",
                        )
                    ),

                    "received_at":
                        received,

                    "body":
                        body,

                    "pdl_id":
                        pdl.id,

                    "pdl_name":
                        pdl.name,

                    "folder":
                        folder_path,

                    "direction":
                        "inbound",

                    "sender_type":
                        sender_type,

                    "engineer_name":
                        engineer.name
                        if engineer
                        else None,

                    "engineer_email":
                        engineer.email
                        if engineer
                        else None,

                    "customer_name":
                        (
                            text(
                                safe_get(
                                    message,
                                    "SenderName",
                                    "",
                                )
                            )
                            if sender_type
                            == "customer"
                            else None
                        ),

                    "responded":
                        bool(response),

                    "response_required":
                        intel.get("response_required", True),

                    "monitoring_excluded":
                        intel.get("monitoring_excluded", False),

                    "exclusion_reason":
                        intel.get("exclusion_reason"),

                    "first_response_at":
                        response,

                    "last_response_at":
                        latest_response,

                    "last_response_engineer_name":
                        latest_response_engineer.name if latest_response_engineer else None,

                    "last_response_engineer_email":
                        latest_response_engineer_email,

                    "response_minutes":
                        response_minutes,

                    "response_diagnostic": response_diag,

                    "response_match_detail": response_diag_detail,

                    "response_source":
                        ("pdl_member_reply" if response_engineer_email else None),

                    "response_engineer_name":
                        response_engineer.name if response_engineer else None,

                    "response_engineer_email":
                        response_engineer_email,

                    "tat_minutes":
                        pdl.tat_minutes,

                    "tat_breached":
                        tat_breached,

                    "synced_at":
                        datetime.now(
                            timezone.utc
                        ),

                    **intel,
                }

                row = upsert_email(
                    data
                )

                # Notifications are deliberately action-oriented.  Sentiment,
                # unhappiness and escalation are intelligence signals only; they
                # do not create a notification by themselves.
                schedule_miss_terms = detect_schedule_miss(subject, body)
                event_is_critical = str(intel.get("severity") or "").lower() == "critical" or str(intel.get("escalation_level") or "").lower() == "critical"
                escalation_is_high = (
                    str(intel.get("escalation_level") or "").lower() in {"high", "critical"}
                    or (intel.get("email_category") == "escalation" and str(intel.get("severity") or "").lower() in {"high", "critical"})
                )

                # Critical operational events notify regardless of sender domain.
                # Team-member/internal identity controls workload/TAT, not event
                # importance. This covers outages, blockers and missed customer
                # calls reported by internal teams.
                if event_is_critical and _email_received_today_ist(received):
                    create_alert(
                        pdl.id, row.id, "critical_escalation", "critical",
                        f"CRITICAL: operational attention required — {subject or '(No subject)'}",
                        ("Signals: " + ", ".join(schedule_miss_terms[:5]) + ". " if schedule_miss_terms else "")
                        + (intel.get("recommended_action") or "Immediate operational attention required."),
                    )
                elif escalation_is_high and _email_received_today_ist(received):
                    create_alert(
                        pdl.id, row.id, "action_required", "high",
                        f"HIGH: escalation / urgent attention — {subject or '(No subject)'}",
                        intel.get("recommended_action") or "High-priority operational review required.",
                    )
                elif schedule_miss_terms and not pdl_member_sender and sender_type != "engineer" and _email_received_today_ist(received):
                    # Configured engineers/team members never create ordinary
                    # schedule-miss notifications from their own coordination
                    # mail. A genuinely critical operational event is handled
                    # by the critical branch above.
                    create_alert(
                        pdl.id, row.id, "schedule_miss", "high",
                        f"Action required: schedule/call miss — {subject or '(No subject)'}",
                        "Signals: " + ", ".join(schedule_miss_terms[:5]),
                    )

                # V10.20.20: do NOT create one Notification Center alert per
                # breached email. Individual Email rows retain tat_breached=True;
                # a single PDL-level summary notification is generated after the
                # sync batch below.

                # Executive/manager presence becomes a notification only when
                # the communication carries concern, escalation, negative
                # sentiment or meaningful operational priority. A routine FYI
                # copied to a manager must not create noise.
                executive_risk = bool(
                    executive_context
                    and (
                        intel.get("escalation_level") in {"medium", "high", "critical"}
                        or intel.get("severity") in {"high", "critical"}
                        or intel.get("sentiment") in {"negative", "concerned"}
                        or intel.get("email_category") == "escalation"
                    )
                )
                if executive_risk and sender_type != "engineer" and _email_received_today_ist(received):
                    # A configured engineer's own mail must not become an
                    # executive/priority notification merely because it uses
                    # urgent/action language. Critical operational events are
                    # handled separately by the critical notification path.
                    executive_names = [
                        (e.designation or e.name or e.email) for e in executive_hits
                    ]
                    if executive_sender and not executive_names:
                        executive_names = [sender_email]
                    create_alert(
                        pdl.id, row.id, "executive_copy", "high" if intel.get("severity") != "critical" else "critical",
                        f"Priority: manager/executive concern or escalation — {subject or '(No subject)'}",
                        "Configured manager/executive: " + ", ".join(executive_names)
                        + ". This message contains an operational concern/escalation and requires attention.",
                    )

                imported += 1

                imported_by_folder[
                    folder_path
                ] = (
                    imported_by_folder.get(
                        folder_path,
                        0,
                    )
                    + 1
                )

                # ------------------------------------------------
                # Commit in batches.
                # This is critical for SQLite.
                # ------------------------------------------------

                if (
                    imported > 0
                    and imported
                    % SYNC_BATCH_SIZE == 0
                ):

                    db.session.commit()

            except Exception as exc:

                try:
                    db.session.rollback()
                except Exception:
                    pass

                # V10.20.17: intelligence/notification/response enrichment must
                # never prevent a matched Outlook message from being imported.
                # The old per-message transaction could roll back the entire row
                # when a secondary stage failed, leaving pdl_matched > 0 but
                # imported = 0. Persist a privacy-safe minimal row as a fallback;
                # the normal AI/enrichment pipeline can update it afterwards.
                fallback_imported = False
                try:
                    fallback_pdl = locals().get("pdl")
                    fallback_message = message
                    fallback_received = received
                    fallback_entry_id = text(safe_get(fallback_message, "EntryID", "")).strip()
                    if fallback_pdl is not None and fallback_entry_id:
                        fallback_sender = (sender_smtp(fallback_message) or "").strip()
                        fallback_sender_lower = fallback_sender.lower()
                        fallback_domain = (
                            fallback_sender_lower.rsplit("@", 1)[-1]
                            if "@" in fallback_sender_lower else ""
                        )
                        fallback_sender_type = (
                            "engineer" if fallback_sender_lower in engineer_contacts
                            else "internal" if fallback_domain in INTERNAL_DOMAINS
                            else "customer" if "@" in fallback_sender_lower
                            else "unknown"
                        )
                        fallback_data = {
                            "entry_id": fallback_entry_id,
                            "internet_message_id": internet_message_id(fallback_message),
                            "conversation_id": cid(fallback_message),
                            "subject": text(safe_get(fallback_message, "Subject", "")),
                            "sender_name": text(safe_get(fallback_message, "SenderName", "")),
                            "sender_email": fallback_sender,
                            "sender_domain": fallback_domain,
                            "recipients": recipients(fallback_message),
                            "cc": text(safe_get(fallback_message, "CC", "")),
                            "received_at": fallback_received,
                            "body": text(safe_get(fallback_message, "Body", "")),
                            "pdl_id": fallback_pdl.id,
                            "pdl_name": fallback_pdl.name,
                            "folder": folder_path,
                            "direction": "inbound",
                            "sender_type": fallback_sender_type,
                            "responded": False,
                            "response_required": fallback_sender_type == "customer",
                            "monitoring_excluded": fallback_sender_type != "customer",
                            "exclusion_reason": "Enrichment fallback after sync-stage error",
                            "tat_breached": False,
                            "tat_minutes": fallback_pdl.tat_minutes,
                            "synced_at": datetime.now(timezone.utc),
                            "email_category": "general",
                            "sentiment": "neutral",
                            "severity": "normal",
                            "escalation_level": "low",
                            "escalation_score": 0,
                        }
                        upsert_email(fallback_data)
                        db.session.commit()
                        imported += 1
                        imported_by_folder[folder_path] = imported_by_folder.get(folder_path, 0) + 1
                        fallback_imported = True
                except Exception as fallback_exc:
                    try:
                        db.session.rollback()
                    except Exception:
                        pass
                    errors.append(
                        f"message_fallback:{type(fallback_exc).__name__}: {str(fallback_exc)[:240]}"
                    )

                # Privacy-safe diagnostics: never persist folder names, subject,
                # body, sender, recipient, or other message content in sync errors.
                errors.append(
                    f"message_processing:{type(exc).__name__}: {str(exc)[:240]}"
                    + ("; fallback_imported" if fallback_imported else "")
                )

        # ----------------------------------------------------
        # TAT SUMMARY NOTIFICATIONS
        # ----------------------------------------------------
        # Individual email rows remain TAT-breached for operational reporting,
        # but Notification Center receives only a PDL-level delta.
        try:
            from sqlalchemy import and_
            from datetime import date
            from zoneinfo import ZoneInfo

            ist = ZoneInfo("Asia/Kolkata")
            today_key = datetime.now(ist).date().isoformat()

            for summary_pdl in pdls:
                breached_rows = (
                    Email.query
                    .filter(
                        Email.pdl_id == summary_pdl.id,
                        Email.tat_breached.is_(True),
                        Email.response_required.is_(True),
                        Email.monitoring_excluded.is_(False),
                        Email.received_at.isnot(None),
                    )
                    .order_by(Email.received_at.asc())
                    .all()
                )
                breached_today = [
                    row for row in breached_rows
                    if _email_received_today_ist(row.received_at)
                ]
                count = len(breached_today)

                # Existing V10.20.19 individual alerts are converted into audit
                # history so they cannot continue flooding the notification feed.
                try:
                    Alert.query.filter(
                        Alert.pdl_id == summary_pdl.id,
                        Alert.alert_type == "tat_breach",
                        Alert.email_id.isnot(None),
                        Alert.read.is_(False),
                    ).update(
                        {Alert.read: True},
                        synchronize_session=False,
                    )
                except Exception:
                    pass

                state_key = f"tat_summary_notified:{summary_pdl.id}:{today_key}"
                previous = int(get_sync_state(state_key) or 0)

                if count > previous:
                    # A new delta must produce a new notification id so browser
                    # clients using after_id receive it. Close the previous
                    # unread PDL summary before creating the updated summary.
                    try:
                        Alert.query.filter(
                            Alert.pdl_id == summary_pdl.id,
                            Alert.alert_type == "tat_breach",
                            Alert.email_id.is_(None),
                            Alert.read.is_(False),
                        ).update(
                            {Alert.read: True},
                            synchronize_session=False,
                        )
                    except Exception:
                        pass

                    delta = count - previous
                    if previous <= 0:
                        title = f"🔴 {summary_pdl.name} — {count} emails breached TAT"
                    else:
                        title = (
                            f"🔴 {summary_pdl.name} — {delta} new emails breached TAT "
                            f"(total {count})"
                        )
                    message = (
                        f"{count} email(s) for {summary_pdl.name} currently have breached TAT. "
                        "Open Alerts to review and action the underlying email list."
                    )
                    create_alert(
                        summary_pdl.id,
                        None,
                        "tat_breach",
                        "high",
                        title,
                        message,
                    )
                    set_sync_state(state_key, str(count))
        except Exception as exc:
            log_event(
                "WARN",
                "tat_notification_summary",
                "TAT summary notification reconciliation failed.",
                str(exc)[:1000],
            )

        set_sync_state("sync_progress", f"Committing {imported} imported email(s) and reconciling responses")
        log_event("INFO", "outlook_sync_progress", f"SYNC: committing {imported} imported email(s) and reconciling responses.")
        # ----------------------------------------------------
        # FINAL COMMIT + DATABASE THREAD RECONCILIATION
        # ----------------------------------------------------

        db.session.commit()
        response_reconciled = reconcile_response_state_from_database(pdls, cutoff)

        now_iso = datetime.now(
            timezone.utc
        ).isoformat()

        set_sync_state(
            "last_sync",
            now_iso,
        )

        set_sync_state("sync_progress", "Complete")
        set_sync_state(
            "last_sync_result",
            (
                f"Imported {imported}, "
                f"matched {matched}, "
                f"errors {len(errors)}"
            ),
        )

        timings["total"] = round(time.perf_counter() - sync_started, 3)
        if SYNC_TIMING_LOG:
            try:
                log_event(
                    "INFO",
                    "outlook_sync_timing",
                    f"Sync completed in {timings['total']}s",
                    json.dumps(timings, default=str),
                )
            except Exception:
                pass

        return {

            "store":
                store_name,

            "inbox":
                inbox_name,

            "sent":
                sent_name,

            "inbox_scanned":
                len(inbox_candidates),

            "sent_scanned":
                len(sent_items),

            "pdl_matched":
                matched,

            "imported":
                imported,

            "duplicates_ignored":
                duplicates_ignored,

            "automatic_ignored":
                automatic_ignored,

            "engineer_ignored":
                engineer_ignored,

            "engineer_response_candidates":
                sum(len(items) for items in engineer_responses_by_pdl.values()) + len(engineer_sent_items),
            "engineer_sent_items_scanned": len(engineer_sent_items),
            "response_reconciled": response_reconciled,

            "errors":
                errors,

            "cutoff":
                cutoff.isoformat(),

            "force_full":
                bool(force_full),

            "timings":
                timings,

            "discovered_members_by_pdl":
                discovered_members_by_pdl,

            "initial_member_discovery_pdl_ids":
                discovery_pending_pdl_ids,

            "member_discovery_mode":
                "initial_sync_only_per_pdl",

            "overlap_minutes":
                SYNC_OVERLAP_MINUTES,

            "folders_scanned":
                len(folder_stats),

            "folder_stats":
                folder_stats,

            "mail_sources":
                source_candidate_counts,

            "pdl_source_status":
                pdl_source_status,

            "matched_by_pdl":
                matched_by_pdl,

            "imported_by_folder":
                imported_by_folder,
        }

    except Exception as exc:

        try:
            db.session.rollback()
        except Exception:
            pass
        try:
            set_sync_state("sync_progress", f"Failed: {str(exc)[:240]}")
        except Exception:
            pass

        raise

    finally:

        try:
            db.session.remove()
        except Exception:
            pass

        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


# ============================================================
# DISCOVER
# ============================================================

def discover():

    com_initialized = False
    if not graph_enabled():
        if pythoncom is None:
            raise RuntimeError(
                "Microsoft Graph mailbox is not configured. Open Settings > Microsoft Graph Mailbox."
            )
        pythoncom.CoInitialize()
        com_initialized = True

    try:

        _, namespace = connect_outlook()

        result = []

        for i in range(
            1,
            namespace.Stores.Count + 1,
        ):

            try:

                store = (
                    namespace
                    .Stores
                    .Item(i)
                )

                name = text(
                    safe_get(
                        store,
                        "DisplayName",
                        "",
                    )
                )

                root = get_store_root(
                    store
                )

                folders = []

                if root:

                    folder_list = (
                        collect_mail_folders(
                            root
                        )
                    )

                    folders = [
                        path
                        for _, path
                        in folder_list
                    ]

                result.append({

                    "name":
                        name,

                    "archive":
                        is_archive(name),

                    "folders":
                        folders,

                })

            except Exception:
                pass

        return result

    finally:

        if com_initialized and pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

def sync_outlook_background_safe(app=None):
    """Compatibility entry point for background/scheduler callers."""
    from autosync_service import run_reliable_sync
    return run_reliable_sync(app=app, reason="background")