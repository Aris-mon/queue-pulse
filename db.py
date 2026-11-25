
from datetime import datetime, timezone
from pathlib import Path
import os

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import UniqueConstraint, Index, inspect as sa_inspect, event


# ============================================================
# DATABASE
# ============================================================

db = SQLAlchemy()

BASE_DIR = Path(__file__).resolve().parent

INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DB_PATH = INSTANCE_DIR / "pdl_intelligence.db"


# SQLite connection tuning is installed lazily when Flask initializes the
# engine.
def configure_sqlite_engine(engine):
    try:
        if engine.dialect.name != "sqlite":
            return
        @event.listens_for(engine, "connect")
        def _sqlite_connect(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()
    except Exception:
        pass


# ============================================================
# USER <-> PDL ASSOCIATION
# ============================================================

user_pdl = db.Table(
    "user_pdl",

    db.Column(
        "user_id",
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),

    db.Column(
        "pdl_id",
        db.Integer,
        db.ForeignKey("pdls.id", ondelete="CASCADE"),
        primary_key=True,
    ),

    UniqueConstraint(
        "user_id",
        "pdl_id",
        name="uq_user_pdl",
    ),
)




# ============================================================
# USER <-> TEAM GROUP ASSOCIATION (MANAGER SCOPE)
# ============================================================

user_team_group = db.Table(
    "user_team_group",
    db.Column(
        "user_id",
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "group_id",
        db.Integer,
        db.ForeignKey("team_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    UniqueConstraint(
        "user_id",
        "group_id",
        name="uq_user_team_group",
    ),
)


# ============================================================
# USER
# ============================================================

class User(UserMixin, db.Model):

    __tablename__ = "users"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(160),
        nullable=False,
    )

    email = db.Column(
        db.String(320),
        nullable=False,
        unique=True,
        index=True,
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False,
    )

    role = db.Column(
        db.String(40),
        nullable=False,
        default="analyst",
        index=True,
    )

    active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    last_login_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    pdls = db.relationship(
        "PDL",
        secondary=user_pdl,
        back_populates="users",
    )

    # Team scope is used only for users with role="manager". A manager sees
    # PDLs belonging to these teams and can edit only these team records.
    managed_teams = db.relationship(
        "TeamGroup",
        secondary=user_team_group,
        back_populates="managers",
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(
            self.password_hash,
            password,
        )

    def __repr__(self):
        return (
            f"<User id={self.id} "
            f"email={self.email!r} "
            f"role={self.role!r}>"
        )


# ============================================================
# PDL
# ============================================================

class PDL(db.Model):

    __tablename__ = "pdls"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(200),
        nullable=False,
        index=True,
    )

    address = db.Column(
        db.String(320),
        nullable=False,
        unique=True,
        index=True,
    )

    team = db.Column(
        db.String(160),
        nullable=True,
        index=True,
    )

    owner_name = db.Column(
        db.String(160),
        nullable=True,
    )

    owner_email = db.Column(
        db.String(320),
        nullable=True,
        index=True,
    )

    tat_minutes = db.Column(
        db.Integer,
        nullable=False,
        default=240,
    )

    active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    users = db.relationship(
        "User",
        secondary=user_pdl,
        back_populates="pdls",
    )

    def __repr__(self):
        return (
            f"<PDL id={self.id} "
            f"name={self.name!r} "
            f"address={self.address!r}>"
        )




# ============================================================
# ENGINEER GROUPS / PDL MEMBERSHIP
# ============================================================

class EngineerGroup(db.Model):
    __tablename__ = "engineer_groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, unique=True, index=True)
    pdl_id = db.Column(db.Integer, db.ForeignKey("pdls.id", ondelete="SET NULL"), nullable=True, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    pdl = db.relationship("PDL", foreign_keys=[pdl_id])
    engineers = db.relationship("Engineer", back_populates="group", lazy=True)

    def __repr__(self):
        return f"<EngineerGroup id={self.id} name={self.name!r} pdl_id={self.pdl_id!r}>"


# ============================================================
# TEAM / PDL / EXECUTIVE ASSOCIATION TABLES
# Defined before Engineer so SQLAlchemy relationship declarations
# can reference them during model class construction.
# ============================================================

team_group_pdl = db.Table(
    "team_group_pdl",
    db.Column("group_id", db.Integer, db.ForeignKey("team_groups.id", ondelete="CASCADE"), primary_key=True),
    db.Column("pdl_id", db.Integer, db.ForeignKey("pdls.id", ondelete="CASCADE"), primary_key=True),
    UniqueConstraint("group_id", "pdl_id", name="uq_team_group_pdl"),
)

# PDL access explicitly granted to a monitoring slot. This is separate from
# engineer PDL access so a slot can be limited to the PDLs it is intended to monitor.
monitoring_slot_pdl = db.Table(
    "monitoring_slot_pdl",
    db.Column("slot_id", db.Integer, db.ForeignKey("monitoring_slots.id", ondelete="CASCADE"), primary_key=True),
    db.Column("pdl_id", db.Integer, db.ForeignKey("pdls.id", ondelete="CASCADE"), primary_key=True),
    UniqueConstraint("slot_id", "pdl_id", name="uq_monitoring_slot_pdl"),
)

team_group_engineer = db.Table(
    "team_group_engineer",
    db.Column("group_id", db.Integer, db.ForeignKey("team_groups.id", ondelete="CASCADE"), primary_key=True),
    db.Column("engineer_id", db.Integer, db.ForeignKey("engineers.id", ondelete="CASCADE"), primary_key=True),
    UniqueConstraint("group_id", "engineer_id", name="uq_team_group_engineer"),
)

engineer_pdl_access = db.Table(
    "engineer_pdl_access",
    db.Column("engineer_id", db.Integer, db.ForeignKey("engineers.id", ondelete="CASCADE"), primary_key=True),
    db.Column("pdl_id", db.Integer, db.ForeignKey("pdls.id", ondelete="CASCADE"), primary_key=True),
    UniqueConstraint("engineer_id", "pdl_id", name="uq_engineer_pdl_access"),
)

executive_group_member = db.Table(
    "executive_group_member",
    db.Column("group_id", db.Integer, db.ForeignKey("executive_groups.id", ondelete="CASCADE"), primary_key=True),
    db.Column("engineer_id", db.Integer, db.ForeignKey("engineers.id", ondelete="CASCADE"), primary_key=True),
    UniqueConstraint("group_id", "engineer_id", name="uq_executive_group_member"),
)


# ============================================================
# ENGINEER
# ============================================================

class Engineer(db.Model):

    __tablename__ = "engineers"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(160),
        nullable=False,
    )

    email = db.Column(
        db.String(320),
        nullable=False,
        unique=True,
        index=True,
    )

    team = db.Column(
        db.String(160),
        nullable=True,
        index=True,
    )

    designation = db.Column(
        db.String(160),
        nullable=True,
    )

    is_executive = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    # Optional operational group. A group can be linked to one PDL so the
    # collector knows which mapped engineers are valid responders for that PDL.
    group_id = db.Column(
        db.Integer,
        db.ForeignKey("engineer_groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Explicit contact role keeps responder engineers separate from leadership.
    # manager/executive mail is monitored for concern/escalation; responder mail
    # is ignored as workload but retained transiently for response/TAT matching.
    contact_role = db.Column(
        db.String(30),
        nullable=False,
        default="engineer",
        index=True,
    )

    group = db.relationship("EngineerGroup", back_populates="engineers", foreign_keys=[group_id])

    active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    team_groups = db.relationship("TeamGroup", secondary=team_group_engineer, back_populates="engineers")
    pdl_access = db.relationship("PDL", secondary=engineer_pdl_access, backref=db.backref("authorized_engineers", lazy=True))
    executive_groups = db.relationship("ExecutiveGroup", secondary=executive_group_member, back_populates="members")

    def __repr__(self):
        return (
            f"<Engineer id={self.id} "
            f"email={self.email!r}>"
        )




class TeamGroup(db.Model):
    __tablename__ = "team_groups"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, unique=True, index=True)
    manager_name = db.Column(db.String(160), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    engineers = db.relationship("Engineer", secondary=team_group_engineer, back_populates="team_groups")
    pdls = db.relationship("PDL", secondary=team_group_pdl, backref=db.backref("team_groups", lazy=True))
    managers = db.relationship("User", secondary=user_team_group, back_populates="managed_teams")
    slots = db.relationship("MonitoringSlot", back_populates="group", cascade="all, delete-orphan", lazy=True)

class MonitoringSlot(db.Model):
    __tablename__ = "monitoring_slots"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("team_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    group = db.relationship("TeamGroup", back_populates="slots")
    pdls = db.relationship("PDL", secondary=monitoring_slot_pdl, backref=db.backref("monitoring_slots", lazy=True))
    assignments = db.relationship("PDLMonitoringAssignment", back_populates="slot", cascade="all, delete-orphan", lazy=True)

class PDLMonitoringAssignmentHistory(db.Model):
    __tablename__ = "pdl_monitoring_assignment_history"
    id = db.Column(db.Integer, primary_key=True)
    pdl_id = db.Column(db.Integer, db.ForeignKey("pdls.id", ondelete="SET NULL"), nullable=True, index=True)
    slot_id = db.Column(db.Integer, db.ForeignKey("monitoring_slots.id", ondelete="SET NULL"), nullable=True, index=True)
    engineer_id = db.Column(db.Integer, db.ForeignKey("engineers.id", ondelete="SET NULL"), nullable=True, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey("team_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    pdl_name = db.Column(db.String(200), nullable=True)
    slot_name = db.Column(db.String(120), nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    group_name = db.Column(db.String(160), nullable=True)
    engineer_name = db.Column(db.String(160), nullable=True)
    engineer_email = db.Column(db.String(320), nullable=True)
    assigned_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ended_at = db.Column(db.DateTime(timezone=True), nullable=True)
    end_reason = db.Column(db.String(40), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    pdl = db.relationship("PDL")
    slot = db.relationship("MonitoringSlot")
    engineer = db.relationship("Engineer")
    group = db.relationship("TeamGroup")


class PDLMonitoringAssignment(db.Model):
    __tablename__ = "pdl_monitoring_assignments"
    id = db.Column(db.Integer, primary_key=True)
    # A PDL may be monitored by multiple slots at the same time/day. The
    # combination (PDL, slot) is the active assignment key; different slots
    # must never overwrite one another.
    pdl_id = db.Column(db.Integer, db.ForeignKey("pdls.id", ondelete="CASCADE"), nullable=False, index=True)
    slot_id = db.Column(db.Integer, db.ForeignKey("monitoring_slots.id", ondelete="CASCADE"), nullable=False, index=True)
    engineer_id = db.Column(db.Integer, db.ForeignKey("engineers.id", ondelete="SET NULL"), nullable=True, index=True)
    assigned_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    pdl = db.relationship("PDL", backref=db.backref("monitoring_assignments", lazy=True, cascade="all, delete-orphan"))
    slot = db.relationship("MonitoringSlot", back_populates="assignments")
    engineer = db.relationship("Engineer")

class MonitoringSlotReportLog(db.Model):
    """Tracks one end-of-slot report per slot/engineer/date so reports are never duplicated."""
    __tablename__ = "monitoring_slot_report_logs"
    id = db.Column(db.Integer, primary_key=True)
    slot_id = db.Column(db.Integer, db.ForeignKey("monitoring_slots.id", ondelete="CASCADE"), nullable=False, index=True)
    engineer_id = db.Column(db.Integer, db.ForeignKey("engineers.id", ondelete="SET NULL"), nullable=True, index=True)
    report_date = db.Column(db.Date, nullable=False, index=True)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(30), nullable=False, default="sent")
    error = db.Column(db.String(500), nullable=True)
    slot = db.relationship("MonitoringSlot")
    engineer = db.relationship("Engineer")
    __table_args__ = (UniqueConstraint("slot_id", "engineer_id", "report_date", name="uq_monitoring_slot_report"),)


class ExecutiveGroup(db.Model):
    __tablename__ = "executive_groups"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, unique=True, index=True)
    description = db.Column(db.String(500), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    members = db.relationship("Engineer", secondary=executive_group_member, back_populates="executive_groups")


# ============================================================
# EMAIL
# ============================================================

class Email(db.Model):

    __tablename__ = "emails"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    entry_id = db.Column(
        db.Text,
        nullable=False,
        unique=True,
        index=True,
    )

    internet_message_id = db.Column(
        db.Text,
        nullable=True,
        index=True,
    )

    conversation_id = db.Column(
        db.Text,
        nullable=True,
        index=True,
    )

    subject = db.Column(
        db.Text,
        nullable=False,
        default="",
    )

    sender_name = db.Column(
        db.String(320),
        nullable=True,
    )

    sender_email = db.Column(
        db.String(320),
        nullable=True,
        index=True,
    )

    recipients = db.Column(
        db.Text,
        nullable=True,
    )

    cc = db.Column(
        db.Text,
        nullable=True,
    )

    received_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    body = db.Column(
        db.Text,
        nullable=True,
    )

    pdl_id = db.Column(
        db.Integer,
        db.ForeignKey("pdls.id"),
        nullable=True,
        index=True,
    )

    pdl_name = db.Column(
        db.String(200),
        nullable=True,
    )

    folder = db.Column(
        db.Text,
        nullable=True,
    )

    direction = db.Column(
        db.String(30),
        nullable=False,
        default="inbound",
        index=True,
    )

    sender_type = db.Column(
        db.String(30),
        nullable=False,
        default="unknown",
        index=True,
    )

    sender_domain = db.Column(
        db.String(320),
        nullable=True,
        index=True,
    )

    engineer_name = db.Column(
        db.String(160),
        nullable=True,
    )

    engineer_email = db.Column(
        db.String(320),
        nullable=True,
        index=True,
    )

    responded = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    # Explicit monitoring semantics. These fields distinguish a message that
    # exists in the mailbox from a message that actually requires operational
    # response tracking. Courtesy/acknowledgement mail can therefore remain
    # visible without polluting workload, response-rate or TAT counts.
    response_required = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    monitoring_excluded = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    # Visibility is deliberately separate from workload exclusion. An email
    # may be informational/recognition (therefore excluded from TAT/workload)
    # while still needing to remain visible in Live Emails and AI Evaluation.
    # AI classification must never make a previously visible email disappear.
    visible_in_monitoring = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    exclusion_reason = db.Column(
        db.String(240),
        nullable=True,
    )

    first_response_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    # Latest qualifying response from a PDL-authorized engineer.
    # first_response_at remains the TAT/response-rate anchor; this field is
    # used for the visible "last responded" timestamp when a thread has
    # multiple valid engineer replies.
    last_response_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    last_response_engineer_name = db.Column(
        db.String(160),
        nullable=True,
    )

    last_response_engineer_email = db.Column(
        db.String(320),
        nullable=True,
        index=True,
    )

    response_minutes = db.Column(
        db.Float,
        nullable=True,
    )

    response_diagnostic = db.Column(
        db.String(80),
        nullable=True,
        index=True,
    )

    response_match_detail = db.Column(
        db.String(500),
        nullable=True,
    )

    response_source = db.Column(
        db.String(80),
        nullable=True,
        index=True,
    )

    # Identity of the PDL-authorized engineer who actually sent the first
    # qualifying response. This is separate from engineer_name/email on the
    # inbound row, which describes the sender of the inbound message.
    response_engineer_name = db.Column(
        db.String(160),
        nullable=True,
    )

    response_engineer_email = db.Column(
        db.String(320),
        nullable=True,
        index=True,
    )

    tat_minutes = db.Column(
        db.Integer,
        nullable=True,
    )

    tat_breached = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    severity = db.Column(
        db.String(30),
        nullable=False,
        default="normal",
        index=True,
    )

    sentiment = db.Column(
        db.String(30),
        nullable=False,
        default="neutral",
        index=True,
    )

    escalation_score = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        index=True,
    )

    escalation_level = db.Column(
        db.String(30),
        nullable=False,
        default="low",
        index=True,
    )

    escalation_reasons = db.Column(
        db.Text,
        nullable=True,
    )

    customer_waiting = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    engineer_action_required = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    product = db.Column(
        db.String(120),
        nullable=True,
        index=True,
    )

    issue_type = db.Column(
        db.String(120),
        nullable=True,
        index=True,
    )

    email_category = db.Column(
        db.String(80),
        nullable=True,
        index=True,
    )

    customer_name = db.Column(
        db.String(240),
        nullable=True,
        index=True,
    )

    account_risk = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        index=True,
    )

    ai_summary = db.Column(
        db.Text,
        nullable=True,
    )

    recommended_action = db.Column(
        db.Text,
        nullable=True,
    )

    synced_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    pdl = db.relationship(
        "PDL",
        backref=db.backref(
            "emails",
            lazy=True,
        ),
    )

    __table_args__ = (

        Index(
            "ix_email_pdl_received",
            "pdl_id",
            "received_at",
        ),

        Index(
            "ix_email_escalation",
            "escalation_level",
            "escalation_score",
        ),

        Index(
            "ix_email_sender_received",
            "sender_type",
            "received_at",
        ),

        Index(
            "ix_email_customer_waiting",
            "customer_waiting",
            "received_at",
        ),

        Index(
            "ix_email_engineer_action",
            "engineer_action_required",
            "received_at",
        ),

        Index(
            "ix_email_response_required",
            "response_required",
            "monitoring_excluded",
            "received_at",
        ),

        Index(
            "ix_email_conversation_received",
            "conversation_id",
            "received_at",
        ),
    )

    def __repr__(self):
        return (
            f"<Email id={self.id} "
            f"subject={self.subject[:60]!r} "
            f"escalation={self.escalation_level!r}>"
        )




# ============================================================
# EVENTS / AUDIT LOG
# ============================================================

class Event(db.Model):

    __tablename__ = "events"

    id = db.Column(db.Integer, primary_key=True)

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    level = db.Column(
        db.String(20),
        nullable=False,
        default="INFO",
        index=True,
    )

    event_type = db.Column(
        db.String(80),
        nullable=False,
        default="system",
        index=True,
    )

    message = db.Column(
        db.Text,
        nullable=False,
    )

    details = db.Column(
        db.Text,
        nullable=True,
    )


def log_event(level, event_type, message, details=None):
    """Write a user-visible/audit event without breaking the main operation."""
    try:
        row = Event(
            level=str(level or "INFO").upper()[:20],
            event_type=str(event_type or "system")[:80],
            message=str(message or "")[:2000],
            details=str(details)[:8000] if details is not None else None,
        )
        db.session.add(row)
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        return None

# ============================================================
# USER NOTIFICATIONS / ALERTS
# ============================================================

class Alert(db.Model):

    __tablename__ = "alerts"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    pdl_id = db.Column(
        db.Integer,
        db.ForeignKey("pdls.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    email_id = db.Column(
        db.Integer,
        db.ForeignKey("emails.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    alert_type = db.Column(
        db.String(80),
        nullable=False,
        default="escalation",
        index=True,
    )
    severity = db.Column(
        db.String(30),
        nullable=False,
        default="medium",
        index=True,
    )
    title = db.Column(db.String(300), nullable=False)
    message = db.Column(db.Text, nullable=True)
    read = db.Column(db.Boolean, nullable=False, default=False, index=True)

    pdl = db.relationship("PDL", foreign_keys=[pdl_id])
    email = db.relationship("Email", foreign_keys=[email_id])


def create_alert(pdl_id, email_id, alert_type, severity, title, message=None):
    """Create an actionable alert with email + open-thread deduplication.

    A single conversation can generate many Outlook message IDs. For critical
    notifications we must not notify the user repeatedly for every reply in the
    same thread while an earlier alert is still unread. A new alert is allowed
    after the previous alert has been acknowledged/read, or for a different alert
    type such as a later TAT breach.
    """
    alert_type = str(alert_type or "escalation")[:80]
    existing = Alert.query.filter_by(email_id=email_id, alert_type=alert_type).first()
    if existing and not existing.read:
        return existing
    if existing and existing.read:
        # Keep historical alert records; a later event on the same email may be
        # actionable again, so do not reuse the old row blindly.
        pass

    # Thread-level suppression applies only to open critical/escalation alerts.
    # This prevents one conversation with multiple inbound messages from
    # producing a notification storm.
    try:
        current_email = Email.query.get(email_id) if email_id else None
        conversation_id = (getattr(current_email, "conversation_id", None) or "").strip()
        if conversation_id and alert_type in {"critical_escalation", "ai_attention", "executive_copy", "schedule_miss"}:
            thread_open = (
                Alert.query.join(Email, Alert.email_id == Email.id)
                .filter(
                    Alert.pdl_id == pdl_id,
                    Alert.alert_type == alert_type,
                    Alert.read.is_(False),
                    Email.conversation_id == conversation_id,
                )
                .order_by(Alert.created_at.desc())
                .first()
            )
            if thread_open:
                return thread_open
            # If a critical alert is already open for the thread, do not create
            # a second lower-level alert for the same conversation.
            if alert_type != "critical_escalation":
                critical_open = (
                    Alert.query.join(Email, Alert.email_id == Email.id)
                    .filter(
                        Alert.pdl_id == pdl_id,
                        Alert.alert_type == "critical_escalation",
                        Alert.read.is_(False),
                        Email.conversation_id == conversation_id,
                    ).first()
                )
                if critical_open:
                    return critical_open
    except Exception:
        pass

    row = Alert(
        pdl_id=pdl_id,
        email_id=email_id,
        alert_type=alert_type,
        severity=str(severity or "medium")[:30],
        title=str(title or "Attention required")[:300],
        message=str(message or "")[:4000],
    )
    db.session.add(row)
    db.session.flush()
    return row



# ============================================================
# AI INTELLIGENCE / AUDIT / FEEDBACK
# ============================================================

class AIAnalysis(db.Model):
    __tablename__ = "ai_analysis"
    id = db.Column(db.Integer, primary_key=True)
    email_id = db.Column(db.Integer, db.ForeignKey("emails.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default="PENDING", index=True)
    provider = db.Column(db.String(80), nullable=True, index=True)
    model = db.Column(db.String(160), nullable=True)
    prompt_version = db.Column(db.String(40), nullable=False, default="v1")
    result_json = db.Column(db.Text, nullable=True)
    confidence = db.Column(db.Float, nullable=False, default=0.0, index=True)
    human_review_required = db.Column(db.Boolean, nullable=False, default=False, index=True)
    fallback_used = db.Column(db.Boolean, nullable=False, default=False, index=True)
    prompt_injection_detected = db.Column(db.Boolean, nullable=False, default=False, index=True)
    injection_flags = db.Column(db.Text, nullable=True)
    processing_ms = db.Column(db.Integer, nullable=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    email = db.relationship("Email", backref=db.backref("ai_analyses", lazy=True, cascade="all, delete-orphan"))

    @property
    def result(self):
        import json
        try:
            return json.loads(self.result_json or "{}")
        except Exception:
            return {}


class AIFeedback(db.Model):
    __tablename__ = "ai_feedback"
    id = db.Column(db.Integer, primary_key=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey("ai_analysis.id", ondelete="CASCADE"), nullable=False, index=True)
    email_id = db.Column(db.Integer, db.ForeignKey("emails.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    verdict = db.Column(db.String(30), nullable=False)
    field_name = db.Column(db.String(80), nullable=True)
    original_prediction = db.Column(db.Text, nullable=True)
    human_correction = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)


class AITrainingExample(db.Model):
    __tablename__ = "ai_training_examples"

    id = db.Column(db.Integer, primary_key=True)
    email_id = db.Column(db.Integer, db.ForeignKey("emails.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    base_model = db.Column(db.String(160), nullable=False, default="qwen3:8b")
    labels_json = db.Column(db.Text, nullable=False)
    example_json = db.Column(db.Text, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    email = db.relationship("Email", backref=db.backref("ai_training_examples", lazy=True, cascade="all, delete-orphan"))


class AIUploadedTrainingSample(db.Model):
    """Human-validated training sample imported from CSV/JSON.

    Kept separate from operational Email rows so bulk training imports never
    contaminate mailbox metrics, reports, TAT, alerts, or live-email views.
    """
    __tablename__ = "ai_uploaded_training_samples"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    base_model = db.Column(db.String(160), nullable=False, default="qwen3:8b")
    labels_json = db.Column(db.Text, nullable=False)
    example_json = db.Column(db.Text, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    source_name = db.Column(db.String(260), nullable=True)
    source_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)


class AIModelLog(db.Model):
    __tablename__ = "ai_model_logs"
    id = db.Column(db.Integer, primary_key=True)
    email_id = db.Column(db.Integer, db.ForeignKey("emails.id", ondelete="SET NULL"), nullable=True, index=True)
    provider = db.Column(db.String(80), nullable=True, index=True)
    model = db.Column(db.String(160), nullable=True)
    prompt_version = db.Column(db.String(40), nullable=True)
    confidence = db.Column(db.Float, nullable=True, index=True)
    processing_ms = db.Column(db.Integer, nullable=True)
    fallback_used = db.Column(db.Boolean, nullable=False, default=False, index=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

# ============================================================
# SYNC STATE
# ============================================================

class SyncState(db.Model):

    __tablename__ = "sync_state"

    key = db.Column(
        db.String(80),
        primary_key=True,
    )

    value = db.Column(
        db.Text,
        nullable=True,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


# ============================================================
# DATABASE URI
# ============================================================

def get_database_uri():

    configured = os.getenv(
        "DATABASE_URL",
        "",
    ).strip()

    if not configured:

        return (
            "sqlite:///"
            + DEFAULT_DB_PATH.resolve().as_posix()
        )

    if configured.lower().startswith("sqlite:///"):

        raw_path = configured[
            len("sqlite:///"):
        ]

        raw_path = raw_path.strip("\"'")

        if not raw_path:

            db_path = DEFAULT_DB_PATH

        else:

            candidate = Path(raw_path)

            if candidate.is_absolute():

                db_path = candidate

            else:

                db_path = BASE_DIR / candidate

        db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        return (
            "sqlite:///"
            + db_path.resolve().as_posix()
        )

    return configured


# ============================================================
# EXISTING DATABASE MIGRATION
# ============================================================


def ensure_email_category_schema():
    """Backward-compatible schema upgrades for existing installations.

    create_all() creates new tables but does not add columns to existing ones.
    This keeps the existing database and data intact while adding only fields
    required by the enhancement package.
    """
    try:
        from sqlalchemy import inspect
        inspector = inspect(db.engine)

        email_columns = {c["name"] for c in inspector.get_columns("emails")}
        if "email_category" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN email_category VARCHAR(80)"
            ))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_emails_email_category "
                "ON emails (email_category)"
            ))

        if "response_required" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN response_required BOOLEAN NOT NULL DEFAULT 1"
            ))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_emails_response_required "
                "ON emails (response_required)"
            ))

        if "monitoring_excluded" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN monitoring_excluded BOOLEAN NOT NULL DEFAULT 0"
            ))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_emails_monitoring_excluded "
                "ON emails (monitoring_excluded)"
            ))

        if "visible_in_monitoring" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN visible_in_monitoring BOOLEAN NOT NULL DEFAULT 1"
            ))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_emails_visible_in_monitoring "
                "ON emails (visible_in_monitoring)"
            ))
        # Existing mail must remain visible. This also repairs databases from
        # pre-V10.19.1 where no visibility field existed.
        db.session.execute(db.text(
            "UPDATE emails SET visible_in_monitoring = 1 WHERE visible_in_monitoring IS NULL"
        ))

        if "exclusion_reason" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN exclusion_reason VARCHAR(240)"
            ))

        pdl_columns = {c["name"] for c in inspector.get_columns("pdls")}
        if "owner_name" not in pdl_columns:
            db.session.execute(db.text(
                "ALTER TABLE pdls ADD COLUMN owner_name VARCHAR(160)"
            ))
        if "owner_email" not in pdl_columns:
            db.session.execute(db.text(
                "ALTER TABLE pdls ADD COLUMN owner_email VARCHAR(320)"
            ))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_pdls_owner_email ON pdls (owner_email)"
            ))

        engineer_columns = {c["name"] for c in inspector.get_columns("engineers")}
        if "designation" not in engineer_columns:
            db.session.execute(db.text(
                "ALTER TABLE engineers ADD COLUMN designation VARCHAR(160)"
            ))
        if "is_executive" not in engineer_columns:
            db.session.execute(db.text(
                "ALTER TABLE engineers ADD COLUMN is_executive BOOLEAN NOT NULL DEFAULT 0"
            ))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_engineers_is_executive ON engineers (is_executive)"
            ))
        if "group_id" not in engineer_columns:
            db.session.execute(db.text(
                "ALTER TABLE engineers ADD COLUMN group_id INTEGER"
            ))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_engineers_group_id ON engineers (group_id)"
            ))
        if "contact_role" not in engineer_columns:
            db.session.execute(db.text(
                "ALTER TABLE engineers ADD COLUMN contact_role VARCHAR(30) NOT NULL DEFAULT 'engineer'"
            ))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_engineers_contact_role ON engineers (contact_role)"
            ))

        if "response_diagnostic" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN response_diagnostic VARCHAR(80)"
            ))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_emails_response_diagnostic ON emails (response_diagnostic)"
            ))
        if "response_match_detail" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN response_match_detail VARCHAR(500)"
            ))

        if "response_source" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN response_source VARCHAR(80)"
            ))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_emails_response_source ON emails (response_source)"
            ))

        if "response_engineer_name" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN response_engineer_name VARCHAR(160)"
            ))

        if "response_engineer_email" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN response_engineer_email VARCHAR(320)"
            ))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_emails_response_engineer_email "
                "ON emails (response_engineer_email)"
            ))

        # Latest qualifying PDL-member response fields.
        if "last_response_at" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN last_response_at DATETIME"
            ))
        if "last_response_engineer_name" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN last_response_engineer_name VARCHAR(160)"
            ))
        if "last_response_engineer_email" not in email_columns:
            db.session.execute(db.text(
                "ALTER TABLE emails ADD COLUMN last_response_engineer_email VARCHAR(320)"
            ))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_emails_last_response_engineer_email "
                "ON emails (last_response_engineer_email)"
            ))

        # Seed the new visible "last responded" fields from the historical
        # first response for existing rows. A subsequent Outlook sync replaces
        # them with the true latest qualifying PDL-member response.
        try:
            db.session.execute(db.text(
                "UPDATE emails SET last_response_at = first_response_at "
                "WHERE last_response_at IS NULL AND first_response_at IS NOT NULL"
            ))
            db.session.execute(db.text(
                "UPDATE emails SET last_response_engineer_name = response_engineer_name "
                "WHERE last_response_engineer_name IS NULL AND response_engineer_name IS NOT NULL"
            ))
            db.session.execute(db.text(
                "UPDATE emails SET last_response_engineer_email = response_engineer_email "
                "WHERE last_response_engineer_email IS NULL AND response_engineer_email IS NOT NULL"
            ))
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Backfill monitoring semantics for legacy rows. Recognition/courtesy
        # mail is not part of operational workload; customer/internal messages
        # otherwise remain tracked until the next Outlook sync applies the full
        # response-requirement classifier.
        try:
            db.session.execute(db.text(
                "UPDATE emails SET response_required = 0, monitoring_excluded = 1, "
                "exclusion_reason = 'Recognition / courtesy communication' "
                "WHERE lower(coalesce(email_category,'')) = 'recognition'"
            ))
            db.session.execute(db.text(
                "UPDATE emails SET response_required = 1, monitoring_excluded = 0, "
                "exclusion_reason = NULL "
                "WHERE lower(coalesce(email_category,'')) <> 'recognition' OR email_category IS NULL"
            ))
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Re-analyze legacy rows with the current intelligence model. This is
        # intentionally broader than the old "only if NULL" backfill because
        # sentiment/category rules have been sharpened over time. It corrects
        # previously mis-tagged appreciation mail already stored in the DB.
        from intelligence import detect_email_category, detect_response_requirement
        from local_ai_agent import analyze_email
        rows = Email.query.all()
        configured_engineer_emails = {
            (e.email or "").strip().lower()
            for e in Engineer.query.filter_by(active=True).all()
            if getattr(e, "email", None)
        }
        changed = 0
        for row in rows:
            agent = analyze_email(row.subject, row.body)
            category = detect_email_category(row.subject, row.body)
            row_sender = (row.sender_email or "").strip().lower()
            # Existing databases may contain older rows stored as "internal"
            # even though the sender is now configured as an engineer. Correct
            # those rows during startup as well as during future Outlook syncs.
            effective_sender_type = (
                "engineer" if row_sender in configured_engineer_emails
                else (row.sender_type or "customer")
            )
            required, reason = detect_response_requirement(
                row.subject, row.body,
                sender_type=effective_sender_type,
                sentiment=agent.sentiment,
                email_category=category,
                executive_context=False,
            )
            if row.sender_type != effective_sender_type:
                row.sender_type = effective_sender_type
                changed += 1
            if row.response_required != required:
                row.response_required = required
                changed += 1
            if row.monitoring_excluded != (not required):
                row.monitoring_excluded = not required
                changed += 1
            if row.exclusion_reason != reason:
                row.exclusion_reason = reason
                changed += 1
            summary = (
                f"Sentiment: {agent.sentiment}. Emotion: {agent.emotion}. "
                f"Customer requirement: {agent.customer_requirement}. "
                f"AI confidence: {agent.confidence}%."
            )
            if row.sentiment != agent.sentiment:
                row.sentiment = agent.sentiment
                changed += 1
            if row.email_category != category:
                row.email_category = category
                changed += 1
            if not row.ai_summary or row.ai_summary.startswith("Sentiment:"):
                if row.ai_summary != summary:
                    row.ai_summary = summary
                    changed += 1
        if changed:
            db.session.commit()
    except Exception:
        db.session.rollback()
        raise


# ============================================================
# DATABASE INITIALIZATION
# ============================================================


def ensure_monitoring_assignment_schema():
    """Upgrade the V10.4 schema so one PDL can have many active slot assignments.

    V10.4 accidentally made pdl_id unique. Existing SQLite databases therefore
    need a small table rebuild; PostgreSQL databases only need the unique
    constraint dropped. Data is copied intact.
    """
    try:
        inspector = sa_inspect(db.engine)
        if "pdl_monitoring_assignments" not in inspector.get_table_names():
            return
        dialect = db.engine.dialect.name
        if dialect == "sqlite":
            with db.engine.begin() as conn:
                rows = conn.exec_driver_sql("PRAGMA index_list('pdl_monitoring_assignments')").fetchall()
                needs_rebuild = False
                for row in rows:
                    # SQLite returns seq, name, unique, origin, partial.
                    idx_name = row[1]
                    is_unique = bool(row[2])
                    cols = [r[2] for r in conn.exec_driver_sql(f'PRAGMA index_info("{idx_name}")').fetchall()]
                    if is_unique and cols == ["pdl_id"]:
                        needs_rebuild = True
                        break
                if needs_rebuild:
                    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
                    conn.exec_driver_sql("DROP TABLE IF EXISTS pdl_monitoring_assignments_new")
                    conn.exec_driver_sql("""
                        CREATE TABLE IF NOT EXISTS pdl_monitoring_assignments_new (
                            id INTEGER PRIMARY KEY,
                            pdl_id INTEGER NOT NULL,
                            slot_id INTEGER NOT NULL,
                            engineer_id INTEGER,
                            assigned_at DATETIME NOT NULL,
                            FOREIGN KEY(pdl_id) REFERENCES pdls(id) ON DELETE CASCADE,
                            FOREIGN KEY(slot_id) REFERENCES monitoring_slots(id) ON DELETE CASCADE,
                            FOREIGN KEY(engineer_id) REFERENCES engineers(id) ON DELETE SET NULL
                        )
                    """)
                    conn.exec_driver_sql("""
                        INSERT INTO pdl_monitoring_assignments_new (id, pdl_id, slot_id, engineer_id, assigned_at)
                        SELECT id, pdl_id, slot_id, engineer_id, assigned_at
                        FROM pdl_monitoring_assignments
                    """)
                    conn.exec_driver_sql("DROP TABLE pdl_monitoring_assignments")
                    conn.exec_driver_sql("ALTER TABLE pdl_monitoring_assignments_new RENAME TO pdl_monitoring_assignments")
                    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pdl_monitoring_assignments_pdl_id ON pdl_monitoring_assignments (pdl_id)")
                    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pdl_monitoring_assignments_slot_id ON pdl_monitoring_assignments (slot_id)")
                    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_pdl_monitoring_assignments_engineer_id ON pdl_monitoring_assignments (engineer_id)")
                    conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        elif dialect == "postgresql":
            with db.engine.begin() as conn:
                constraint = conn.exec_driver_sql("""
                    SELECT conname
                    FROM pg_constraint
                    WHERE conrelid = 'pdl_monitoring_assignments'::regclass
                      AND contype = 'u'
                      AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid='pdl_monitoring_assignments'::regclass AND attname='pdl_id')]
                    LIMIT 1
                """).scalar()
                if constraint:
                    safe = constraint.replace('"', '""')
                    conn.exec_driver_sql(f'ALTER TABLE pdl_monitoring_assignments DROP CONSTRAINT "{safe}"')
    except Exception:
        db.session.rollback()


def init_db(app=None):

    if app is not None:

        database_uri = get_database_uri()

        app.config[
            "SQLALCHEMY_DATABASE_URI"
        ] = database_uri

        app.config[
            "SQLALCHEMY_TRACK_MODIFICATIONS"
        ] = False

        db.init_app(app)

        with app.app_context():
            configure_sqlite_engine(db.engine)
            db.create_all()
            ensure_email_category_schema()
            ensure_monitoring_assignment_schema()

    else:

        try:
            configure_sqlite_engine(db.engine)
        except Exception:
            pass
        db.create_all()
        ensure_email_category_schema()
        ensure_monitoring_assignment_schema()


# ============================================================
# ADMIN BOOTSTRAP
# ============================================================

def ensure_admin(config):

    admin_email = (
        config.ADMIN_EMAIL
        .strip()
        .lower()
    )

    admin = (
        User.query
        .filter_by(email=admin_email)
        .first()
    )

    if not admin:

        admin = User(
            name=config.ADMIN_NAME,
            email=admin_email,
            role="admin",
            active=True,
        )

        admin.set_password(
            config.ADMIN_PASSWORD
        )

        db.session.add(admin)
        db.session.commit()

        return True

    return False


# ============================================================
# PDL HELPERS
# ============================================================

def list_pdls():

    return (
        PDL.query
        .filter_by(active=True)
        .order_by(PDL.name)
        .all()
    )


def get_user_pdls(user):

    if not user:
        return []

    if getattr(user, "role", "") == "admin":
        return list_pdls()

    if getattr(user, "role", "") == "manager":
        return sorted(
            {
                pdl
                for group in (getattr(user, "managed_teams", None) or [])
                if group.active
                for pdl in (group.pdls or [])
                if pdl.active
            },
            key=lambda p: (p.name or "").lower(),
        )

    return [
        pdl
        for pdl in user.pdls
        if pdl.active
    ]


def assign_pdl_to_user(user, pdl):

    if pdl not in user.pdls:

        user.pdls.append(pdl)

        db.session.commit()

        return True

    return False


def remove_pdl_from_user(user, pdl):

    if pdl in user.pdls:

        user.pdls.remove(pdl)

        db.session.commit()

        return True

    return False


# ============================================================
# EMAIL UPSERT
# ============================================================

def upsert_email(data):

    entry_id = (
        data.get("entry_id") or ""
    ).strip()

    if not entry_id:

        raise ValueError(
            "Outlook EntryID is required"
        )

    row = (
        Email.query
        .filter_by(entry_id=entry_id)
        .first()
    )

    if not row:

        row = Email(
            entry_id=entry_id
        )

        db.session.add(row)

    preserve_manual_response = bool(
        getattr(row, "responded", False)
        and getattr(row, "response_source", None) == "notification_mark_read"
    )

    for key, value in data.items():

        if hasattr(row, key):
            if preserve_manual_response and key in {
                "responded", "first_response_at", "response_minutes",
                "tat_breached", "response_source"
            }:
                # A manual notification resolution is deliberate user state.
                # Re-sync must never reopen it merely because Outlook contains
                # no engineer response candidate yet.
                continue

            setattr(
                row,
                key,
                value,
            )

    row.updated_at = datetime.now(
        timezone.utc
    )

    db.session.flush()

    # A real response from an engineer who has PDL access closes any linked
    # operational notifications. This keeps the notification badge aligned with
    # the actual response state during Outlook auto-sync.
    if getattr(row, "responded", False) and getattr(row, "response_source", None) == "pdl_member_reply":
        Alert.query.filter(
            Alert.email_id == row.id,
            Alert.read.is_(False),
        ).update({Alert.read: True}, synchronize_session=False)

    return row




# ============================================================
# SYNC STATE
# ============================================================

def set_sync_state(key, value):

    row = SyncState.query.get(key)

    if not row:

        row = SyncState(
            key=key
        )

        db.session.add(row)

    row.value = value

    row.updated_at = datetime.now(
        timezone.utc
    )

    db.session.commit()


def get_sync_state(key):

    row = SyncState.query.get(key)

    return (
        row.value
        if row
        else None
    )

