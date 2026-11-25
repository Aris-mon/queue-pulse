import re
from collections import Counter
from local_ai_agent import analyze_email

INTERNAL_DOMAINS = {"hpe.com"}

PRODUCTS = [
    "Alletra", "Alletra MP", "Primera", "3PAR", "Nimble", "MSA",
    "MSL", "StoreOnce", "Synergy", "ProLiant", "Aruba", "Switch",
    "SAN", "FC", "Fibre Channel", "OneView", "iLO",
]

ISSUE_TYPES = {
    "outage": ["outage", "down", "unavailable", "production down", "service down", "service unavailable"],
    "performance": ["slow", "latency", "performance", "iops", "throughput"],
    "hardware": ["disk", "drive", "controller", "power supply", "fan", "hardware failure"],
    "firmware": ["firmware", "upgrade", "upgrade failed", "patch", "sp"],
    "connectivity": ["connectivity", "connection", "fc", "iscsi", "network", "path down"],
    "capacity": ["capacity", "space", "full", "storage utilization"],
    "installation": ["install", "deployment", "configure", "configuration"],
    "license": ["license", "licensing", "entitlement"],
    "general_support": ["question", "help", "support", "request", "issue"],
}

NEGATIVE = {
    "angry", "frustrated", "unacceptable", "disappointed", "poor support",
    "not happy", "not satisfied", "terrible", "complaint", "still waiting",
    "no response", "very unhappy", "extremely unhappy", "bad experience",
}
POSITIVE = {
    "thanks", "thank you", "appreciate", "appreciation", "appreciated",
    "resolved", "great", "excellent", "pleased", "satisfied", "kudos",
    "praise", "recognition", "recognized", "recognised", "top talent",
    "well done", "great job", "great work", "outstanding", "exceptional",
}

RECOGNITION_TERMS = {
    "appreciation", "appreciate", "appreciated", "thank you", "thanks",
    "kudos", "top talent", "great work", "good work", "great job",
    "excellent work", "excellent job", "outstanding work", "well done",
    "commend", "commended", "praise", "recognition", "recognize",
    "recognized", "recognised", "well deserved", "congratulations",
    "congratulate", "valuable contribution", "strong contribution",
    "credit to", "go the extra mile", "fantastic", "brilliant",
    "exceptional", "superb",
}


# ---------------------------------------------------------------------------
# Email communication intent. This is intentionally separate from issue_type:
# issue_type describes the technical topic, while email_category describes
# what the customer is asking us to do.
# ---------------------------------------------------------------------------
EMAIL_CATEGORY_SIGNALS = {
    "recognition": [
        "appreciation", "kudos", "top talent", "great work", "great job",
        "excellent work", "excellent job", "outstanding work",
        "outstanding job", "well done", "commend", "commended", "praise",
        "recognition", "recognize", "recognized", "recognised", "well deserved",
        "congratulations", "congratulate", "valuable contribution",
        "strong contribution", "successful completion", "successfully completed",
        "excellent support", "great support", "outstanding support",
        "excellent service", "successful delivery", "delivered successfully",
    ],
    "escalation": [
        "escalate", "escalation", "raise this", "raise to management",
        "raise with management", "take this to management", "management escalation",
        "formal complaint", "complaint",
    ],
    "schedule_request": [
        "schedule", "scheduling", "meeting", "call", "arrange a call",
        "book a call", "set up a call", "calendar", "availability",
        "available for a call", "propose a time", "timeline", "plan a call",
    ],
    "update_request": [
        "any update", "an update", "update", "status update", "status",
        "progress", "current status", "latest status", "where do we stand",
        "please update", "provide an update", "let me know the status",
    ],
    "follow_up": [
        "following up", "follow-up", "follow up", "reminder", "second reminder",
        "third reminder", "still waiting", "no response", "no update",
        "checking in", "just checking",
    ],
    "technical_support": [
        "issue", "problem", "error", "failed", "failure", "not working",
        "does not work", "unable to", "cannot", "support", "help",
        "incident", "outage", "down",
    ],
    "information_request": [
        "question", "query", "could you confirm", "please confirm",
        "information", "details", "clarification", "how do i", "how can i",
    ],
}

EMAIL_CATEGORY_LABELS = {
    "recognition": "Recognition",
    "escalation": "Escalation",
    "schedule_request": "Schedule Request",
    "update_request": "Update Request",
    "follow_up": "Follow-up / Reminder",
    "technical_support": "Technical Support",
    "information_request": "Information / Query",
    "general": "General",
}


def _latest_message_text(value):
    """Return only the newest authored portion of an Outlook-style message.

    Category classification must not be influenced by appreciation or other
    wording buried in quoted thread history.
    """
    raw = str(value or "").replace("\r\n", "\n")
    lines = raw.split("\n")
    kept = []
    for line in lines:
        stripped = line.strip()
        low = stripped.lower()
        if re.match(r"^on .+ wrote:$", low):
            break
        if stripped.startswith(">"):
            break
        if low.startswith("-----original message") or low.startswith("begin forwarded message"):
            break
        if re.match(r"^(from|sent|to|cc|subject):", low) and kept:
            break
        kept.append(line)
    return clean_text(strip_signature("\n".join(kept))) or clean_text(strip_signature(raw))


NON_ACTIONABLE_EXACT_PHRASES = (
    "thank you", "thanks", "many thanks", "thanks a lot",
    "appreciate it", "much appreciated", "noted", "acknowledged",
    "received with thanks", "well received", "for your information",
    "fyi", "no action required", "no response required",
)

ACTION_REQUEST_TERMS = (
    "please confirm", "please advise", "please provide", "please share",
    "please schedule", "please arrange", "please check", "please review",
    "please respond", "please let me know", "could you", "can you",
    "would you", "need you to", "request", "required", "action required",
    "waiting for", "awaiting", "follow up", "reminder", "escalat",
    "urgent", "asap", "by eod", "today", "tomorrow", "schedule",
    "availability", "confirm", "status update", "any update",
)


def detect_response_requirement(subject, body, sender_type="customer",
                                sentiment="neutral", email_category=None,
                                executive_context=False):
    """Decide whether the latest authored message creates customer workload.

    A customer email is not automatically actionable. Courtesy, FYI, factual
    confirmations and completion notices are informational unless the message
    contains a concrete request, unresolved risk, follow-up, escalation or
    question. Engineer/internal messages are always excluded from customer
    workload.
    """
    subject_text = clean_text(subject).lower()
    body_text = _latest_message_text(body).lower()
    text_value = f"{subject_text} {body_text}".strip()

    if sender_type == "engineer":
        return False, "Configured engineer / team-member communication"

    explicit_no_action = any(term in text_value for term in NON_ACTIONABLE_EXACT_PHRASES)
    risk_terms = (
        "escalat", "concern", "unacceptable", "frustrat", "complaint",
        "manager", "management", "director", "leadership", "executive",
        "production impact", "business impact", "blocked", "urgent",
        "still waiting", "no response", "no update", "overdue",
        "delay", "delayed", "disappointed", "poor support", "not happy",
        "not satisfied", "frustrated", "angry", "unacceptable", "complaint",
    )
    has_risk = any(term in text_value for term in risk_terms)

    action_terms = (
        "please confirm", "please advise", "please provide", "please share",
        "please schedule", "please arrange", "please book", "please check",
        "please review", "please respond", "please let me know",
        "could you", "can you", "would you", "need you to", "request",
        "action required", "waiting for", "awaiting", "follow up",
        "following up", "reminder", "escalat", "urgent", "asap",
        "availability", "propose a time", "reschedule", "re-schedule",
        "confirm availability", "when can we connect", "any update",
        "any updates", "please update", "provide an update", "status update",
        "please clarify", "could you clarify", "need clarification",
        "not working", "unable to", "cannot", "can't", "failed", "failure",
        "error", "issue", "problem", "outage", "down", "blocked",
    )
    action_scan_text = text_value
    for phrase in ("no action required", "no response required"):
        action_scan_text = action_scan_text.replace(phrase, " ")
    has_action = any(term in action_scan_text for term in action_terms)
    has_question = "?" in text_value and not explicit_no_action

    if explicit_no_action and not has_action and not has_risk and not has_question:
        return False, "Courtesy / acknowledgement / no response required"

    if email_category == "recognition" and not has_action and not has_risk:
        return False, "Recognition / appreciation"

    if sender_type == "internal" and not executive_context:
        return (True, None) if (has_action or has_risk or has_question) else (
            False, "Internal / informational communication"
        )

    if executive_context and (has_risk or sentiment in {"negative", "concerned"} or email_category == "escalation"):
        return True, None

    if sender_type == "customer":
        if has_action or has_risk or has_question or sentiment in {"negative", "concerned"}:
            return True, None
        # AI/rule intent categories that explicitly represent customer work.
        if email_category in {
            "schedule_request", "update_request", "follow_up",
            "technical_support", "information_request", "escalation"
        }:
            return True, None
        return False, "Informational / no customer action"

    return (has_action or has_risk or has_question,
            None if (has_action or has_risk or has_question)
            else "Informational / no action")

def detect_email_category(subject, body, agent=None):
    """Classify the latest authored message by communication intent.

    Category is intentionally intent-first. Words such as "call", "meeting",
    "installation", "support" and "schedule" are common in appreciation mail
    and are not, by themselves, a request. Recognition requires substantive
    appreciation; an actual request/follow-up always outranks a polite closing.
    """
    subject_text = clean_text(subject).lower()
    body_text = _latest_message_text(body).lower()
    full_text = f"{subject_text} {body_text}".strip()
    agent = agent or analyze_email(subject, body)

    schedule_action = (
        "please schedule", "please arrange", "please book", "please set up",
        "schedule a call", "schedule the call", "schedule a meeting",
        "arrange a call", "arrange the call", "book a call", "book the call",
        "set up a call", "set up the call", "propose a time", "propose a slot",
        "confirm availability", "share your availability", "let me know your availability",
        "what time works", "when can we connect", "when are you available",
        "reschedule", "re-schedule", "change the schedule", "move the meeting",
        "postpone the meeting", "cancel the meeting", "cancel the call",
    )
    update_action = (
        "any update", "any updates", "please update", "please provide an update",
        "provide an update", "send an update", "status update", "current status",
        "latest status", "where do we stand", "let me know the status",
        "please share the status", "please advise the status",
    )
    followup_action = (
        "following up", "follow-up", "follow up", "second reminder",
        "third reminder", "reminder", "still waiting", "no response",
        "no update", "checking in", "just checking in",
    )
    info_action = (
        "please confirm", "could you confirm", "can you confirm",
        "please advise", "please provide", "please share", "could you share",
        "can you share", "please clarify", "could you clarify",
        "need clarification", "could you let me know",
    )
    support_action = (
        "not working", "does not work", "doesn't work", "unable to",
        "cannot", "can't", "failed", "failure", "error", "issue",
        "problem", "outage", "down", "blocked", "need help", "need support",
        "please investigate", "please check", "please troubleshoot",
    )

    def has_any(phrases):
        return any(p in full_text for p in phrases)

    # Strong operational intent first.
    if has_any(followup_action):
        return "follow_up"
    # Scheduling is a specific operational intent and outranks generic
    # confirmation/information language such as "please confirm the schedule".
    if has_any(schedule_action) or (
        "schedule" in full_text and ("confirm" in full_text or "availability" in full_text)
    ):
        return "schedule_request"
    if has_any(update_action):
        return "update_request"
    if has_any(support_action):
        return "technical_support"
    if has_any(info_action):
        return "information_request"

    # Explicit escalation in the newest authored message.
    if has_any(EMAIL_CATEGORY_SIGNALS["escalation"]):
        return "escalation"

    # A factual schedule/meeting statement is not a request. Keep it general
    # unless it contains a real scheduling action.
    recognition = bool(
        agent.recognition
        and agent.sentiment == "positive"
    )
    if recognition:
        return "recognition"

    # Technical/support nouns alone do not create a category.
    return "general"


# ---------------------------------------------------------------------------
# Escalation signals. Escalation and severity are intentionally separate:
# an email can be urgent without being an escalation, and vice versa.
# ---------------------------------------------------------------------------
ESCALATION_SIGNALS = {
    "explicit_escalation": (35, [
        "please escalate", "escalate this", "escalate the issue",
        "escalate to management", "raise this", "raise this to management",
        "management escalation", "formal escalation", "formal complaint",
        "customer escalation",
    ]),
    "production_impact": (30, [
        "production down", "outage", "service down", "business impact",
        "production impact", "critical production",
    ]),
    "customer_frustration": (20, [
        "unacceptable", "frustrated", "angry", "disappointed",
        "not happy", "poor support", "complaint", "concern", "concerned",
    ]),
    "repeated_followup": (18, [
        "second reminder", "2nd reminder", "third reminder", "3rd reminder",
        "still waiting", "following up again", "any update", "reminder",
    ]),
    "urgent_language": (12, [
        "urgent", "urgently", "asap", "immediately", "high priority",
        "as soon as possible", "quick response", "quickly",
    ]),
}

# ---------------------------------------------------------------------------
# Priority / severity model.
#
# This is deliberately deterministic and explainable so operations teams can
# understand why an email was promoted. It evaluates subject + body together.
# The old implementation made any negative sentiment "medium", which meant
# phrases such as "please respond as soon as possible" could remain medium.
# ---------------------------------------------------------------------------
PRIORITY_SIGNALS = {
    "critical_impact": (70, [
        "production down", "production is down", "production outage",
        "service down", "service is down", "service unavailable",
        "complete outage", "major outage", "business stopped",
        "business is stopped", "business critical", "critical production",
        "data loss", "data corruption", "security incident", "security breach",
        "ransomware", "customer completely blocked", "completely blocked",
    ]),
    "planned_downtime": (20, [
        "planned downtime", "downtime required", "required downtime",
        "maintenance window", "outage window", "service interruption",
        "will there be downtime", "is downtime required",
    ]),
    "immediate_urgency": (35, [
        "urgent", "urgently", "asap", "a.s.a.p", "immediately",
        "right away", "at the earliest", "as soon as possible",
        "as soon as you can", "quick response", "quickly respond",
        "quick response required", "immediate response", "immediate attention",
        "need a quick response", "need quick response", "respond urgently",
        "time sensitive", "time-sensitive", "today", "before end of day",
        "by eod", "eod today",
    ]),
    "explicit_priority": (30, [
        "high priority", "highest priority", "top priority", "priority 1",
        "p1", "sev1", "sev 1", "critical priority", "critical issue",
        "urgent issue", "action required immediately",
    ]),
    "customer_blocked": (28, [
        "blocked", "blocking us", "blocking customer", "cannot proceed",
        "can't proceed", "unable to proceed", "unable to work",
        "work is blocked", "customer is waiting", "waiting for response",
        "waiting for your response", "awaiting response",
    ]),
    "explicit_escalation": (25, [
        "please escalate", "escalate this", "escalate the issue",
        "escalate to management", "raise this", "raise this to management",
        "management escalation", "formal escalation", "formal complaint",
        "customer escalation",
    ]),
    "repeat_followup": (22, [
        "second reminder", "2nd reminder", "third reminder", "3rd reminder",
        "still waiting", "following up again", "following up", "any update",
        "no response", "no update", "reminder",
    ]),
    "service_impact": (24, [
        "customer impact", "business impact", "production impact",
        "service impact", "users impacted", "users are impacted",
        "multiple users", "customer facing", "customer-facing",
    ]),
    "strong_negative": (18, [
        "unacceptable", "very frustrated", "extremely frustrated", "angry",
        "disappointed", "poor support", "terrible", "complaint", "concern", "concerned",
    ]),
    "technical_failure": (16, [
        "failed", "failure", "not working", "does not work", "doesn't work",
        "error", "error message", "path down", "controller failure",
        "hardware failure",
    ]),
    "customer_meeting_miss": (65, [
        "did not join the call", "didn't join the call", "failed to join the call",
        "missed the call", "missed call", "no one joined", "nobody joined", "no one has joined", "nobody has joined",
        "did not attend the call", "failed to attend the call",
        "engineer did not join", "engineer didn't join", "engineer failed to join",
    ]),
    "schedule_execution_failure": (45, [
        "failed to schedule", "schedule was missed", "not scheduled",
        "was not scheduled", "did not schedule", "failed to attend",
    ]),
}


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def strip_signature(text):
    """Remove common Outlook signature/footer blocks before intent/escalation analysis.

    Signature text is metadata, not the customer's operational request. In
    particular, lines such as "For any escalations, please contact ..." must
    never turn a routine email into an escalation.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    out = []
    signature_trigger = re.compile(
        r"^(?:for\s+(?:(?:any|all)\s+)?)?escalations?\b.*\b(?:please\s+)?contact\b",
        re.I,
    )
    signature_inline = re.compile(
        r"(?:for\s+(?:(?:any|all)\s+)?)?escalations?\s*[,;:-]?\s*(?:please\s+)?contact\b",
        re.I,
    )
    closing = {"regards", "best regards", "kind regards", "thanks & regards",
               "thank you & regards", "warm regards", "best", "thanks"}
    for line in lines:
        low = line.strip().lower()
        if signature_trigger.match(low):
            break
        # Outlook signatures are not always separated cleanly into lines.
        # Once a generic signature phrase is encountered, remove the signature
        # tail instead of allowing "escalation" to become an operational signal.
        inline = signature_inline.search(line)
        if inline:
            prefix = line[:inline.start()].rstrip(" -:;|")
            if prefix:
                out.append(prefix)
            break
        if re.match(r"^escalations?\s*[:|-]", low) and len(out) >= 2:
            break
        if low in closing and len(out) >= 2:
            break
        out.append(line)
    return "\n".join(out).strip()


def email_domain(email):
    value = clean_text(email).lower()
    return value.split("@", 1)[1] if "@" in value else ""


def classify_sender(sender_email, engineer_emails=None):
    email = clean_text(sender_email).lower()
    domain = email_domain(email)
    engineer_emails = {x.lower() for x in (engineer_emails or set())}
    if email in engineer_emails:
        return "engineer"
    if domain in INTERNAL_DOMAINS:
        return "internal"
    if domain:
        return "customer"
    return "unknown"


def detect_sentiment(text):
    """Return the canonical sentiment from the contextual AI agent.

    This prevents technical words (critical, issue, installation, urgent) from
    being mistaken for emotion and gives recognition/appreciation precedence.
    """
    agent = analyze_email("", text)
    return agent.sentiment


def detect_product(text):
    t = clean_text(text).lower()
    hits = []
    for product in PRODUCTS:
        if product.lower() in t:
            hits.append(product)
    return max(hits, key=len) if hits else "Other"


def detect_issue_type(text):
    t = clean_text(text).lower()
    scores = Counter()
    for issue, terms in ISSUE_TYPES.items():
        for term in terms:
            if term in t:
                scores[issue] += 1
    return scores.most_common(1)[0][0] if scores else "general_support"


def _matched_signals(text, signals):
    """Return matched phrases grouped by signal name."""
    t = clean_text(text).lower()
    matches = {}
    for name, (_points, terms) in signals.items():
        found = [term for term in terms if term in t]
        if found:
            matches[name] = found
    return matches


def calculate_escalation(
    text,
    sender_type,
    tat_breached=False,
    response_minutes=None,
    tat_minutes=None,
    recipient_count=0,
):
    t = clean_text(text).lower()
    score = 0
    reasons = []

    for reason, (points, terms) in ESCALATION_SIGNALS.items():
        matched = [term for term in terms if term in t]
        if matched:
            score += points
            reasons.append(f"{reason}: {', '.join(matched[:3])}")

    if sender_type == "customer":
        score += 5
    if recipient_count >= 6:
        score += 8
        reasons.append("large recipient group")

    if tat_breached:
        score += 25
        reasons.append("TAT breached")

    if response_minutes is None and tat_minutes:
        # Aging is handled using received_at by caller where possible.
        pass

    score = min(score, 100)
    if score >= 80:
        level = "critical"
    elif score >= 60:
        level = "high"
    elif score >= 35:
        level = "medium"
    else:
        level = "low"

    return score, level, reasons


def calculate_severity(
    text,
    sender_type,
    sentiment,
    tat_breached=False,
    response_minutes=None,
    tat_minutes=None,
):
    """Classify operational severity from the actual request priority.

    Returns (severity, matched_reasons). Severity is based on the email's
    urgency, impact, blocking condition, escalation language and failure
    indicators, rather than sentiment alone.
    """
    t = clean_text(text).lower()
    matches = _matched_signals(t, PRIORITY_SIGNALS)

    score = 0
    reasons = []

    # Each signal category contributes once. This prevents one phrase repeated
    # many times from dominating the classification.
    for name, (points, _terms) in PRIORITY_SIGNALS.items():
        if name in matches:
            score += points
            reasons.append(f"{name}: {', '.join(matches[name][:3])}")

    if sender_type == "customer":
        score += 8

    if sentiment == "negative":
        score += 10
        reasons.append("negative customer sentiment")
    elif sentiment == "concerned":
        score += 5
        reasons.append("concerned customer sentiment")

    if tat_breached:
        score += 35
        reasons.append("TAT breached")

    # If a response was required inside a very short TAT and there is no
    # response yet, treat that as an operational priority signal.
    if response_minutes is None and tat_minutes is not None:
        if tat_minutes <= 15 and sender_type == "customer":
            score += 10
            reasons.append(f"short customer TAT ({tat_minutes} min)")

    # Actual service outage/unavailability is a critical operational condition.
    # Planned/possible downtime is high-risk but is not treated as an active outage
    # unless the message explicitly says the service is down/unavailable.
    actual_outage = any(x in t for x in (
        "production is down", "production down", "service is down",
        "service unavailable", "service is unavailable", "system is down",
        "system unavailable", "currently down", "currently unavailable",
        "complete outage", "major outage", "production outage",
    ))

    # Strong production/security/data-impact signals are critical by themselves.
    # IMPORTANT: sender type is NOT a severity gate. A configured PDL member
    # can report a customer-facing miss/incident, and that event must still
    # become critical even though the sender is internal/engineer.
    if actual_outage or "critical_impact" in matches or "customer_meeting_miss" in matches:
        if actual_outage and "critical_impact" not in matches:
            reasons.append("actual outage/unavailability detected")
        severity = "critical"
        if "customer_meeting_miss" in matches:
            reasons.append("customer-facing meeting/call miss")
    elif "schedule_execution_failure" in matches and (
        "customer_blocked" in matches or "service_impact" in matches or "production_impact" in matches
    ):
        severity = "critical"
        reasons.append("customer-facing schedule execution failure")
    # Multiple independent urgency/impact signals indicate a high-priority case.
    elif score >= 50 or (
        "immediate_urgency" in matches
        and ("customer_blocked" in matches or "service_impact" in matches)
    ):
        severity = "high"
    elif score >= 22:
        severity = "medium"
    else:
        severity = "normal"

    return severity, reasons



SCHEDULE_MISS_SIGNALS = [
    "missed to schedule", "not scheduled", "was not scheduled",
    "did not schedule", "failed to schedule", "schedule was missed",
    "did not join", "didn't join", "failed to join", "missed the call",
    "missed call", "no one joined", "nobody joined", "no one has joined", "nobody has joined", "did not attend",
    "failed to attend", "customer escalation", "internal escalation",
]


def detect_schedule_miss(subject, body):
    """Detect explicit schedule/call misses described by the sender."""
    text_value = _latest_message_text(body).lower()
    text_value = clean_text(f"{subject}\n{text_value}").lower()
    return [term for term in SCHEDULE_MISS_SIGNALS if term in text_value]

def build_intelligence(
    subject,
    body,
    sender_email,
    sender_type,
    tat_breached=False,
    response_minutes=None,
    tat_minutes=None,
    recipient_count=0,
    executive_recipient_count=0,
    executive_designations=None,
    executive_context=False,
):
    text = clean_text(f"{subject}\n{_latest_message_text(body)}")
    # Embedded local intelligence agent: standard-library only, no cloud/API
    # dependency.  Existing rule-based fields remain the source of truth when
    # they identify stronger risk; the agent enriches emotion and requirement
    # understanding and can promote clearly unhappy/escalating messages.
    agent = analyze_email(subject, body)
    sentiment = agent.sentiment
    # Recognition is a first-class positive communication intent. Do not allow
    # stale/generic rule scores to downgrade it to concerned/negative.
    if agent.recognition:
        sentiment = "positive"
    # Missed customer commitments/calls with waiting/urgency express concern
    # even when the wording is polite and contains no explicit negative adjective.
    latest_lower = text.lower()
    missed_call_context = any(x in latest_lower for x in (
        "no one joined", "nobody joined", "did not join", "didn't join",
        "missed the call", "failed to join", "waiting on the call",
    ))
    if sentiment == "neutral" and missed_call_context:
        sentiment = "concerned"
    product = detect_product(text)
    issue_type = detect_issue_type(text)
    email_category = detect_email_category(subject, body, agent=agent)

    # Operational impact is not an emotion, but a customer-facing outage or
    # downtime risk should never be presented as a bland neutral message.
    # Use CONCERNED for impact/risk wording and reserve NEGATIVE for explicit
    # dissatisfaction. Priority/severity carries the CRITICAL decision.
    operational_risk_text = text.lower()
    actual_outage_context = any(x in operational_risk_text for x in (
        "production is down", "production down", "service is down",
        "service unavailable", "service is unavailable", "system is down",
        "system unavailable", "complete outage", "major outage",
        "production outage", "currently down", "currently unavailable",
    ))
    downtime_risk_context = any(x in operational_risk_text for x in (
        "downtime required", "required downtime", "will there be downtime",
        "is downtime required", "maintenance window", "service interruption",
    ))
    if sentiment == "neutral" and (actual_outage_context or downtime_risk_context):
        sentiment = "concerned"

    score, level, reasons = calculate_escalation(
        text, sender_type, tat_breached, response_minutes, tat_minutes, recipient_count
    )

    if sender_type == "customer" and agent.escalation_risk and not agent.recognition:
        # Promote unhappy/negative customer communication into the operational
        # escalation workflow while keeping the score explainable.
        agent_points = 28 if agent.sentiment == "negative" else 16
        score = min(100, score + agent_points)
        reasons.extend([f"local agent: {r}" for r in agent.reasons[:4]])
        if score >= 80:
            level = "critical"
        elif score >= 60:
            level = "high"
        elif score >= 35:
            level = "medium"
    elif sender_type in {"engineer", "internal"} and agent.escalation_risk:
        # Internal/PDL-member mail is not customer workload, but it can contain
        # critical operational evidence (missed customer call, outage, blocker,
        # escalation). Never discard that signal merely because the sender is
        # internal.
        reasons.extend([f"team event: {r}" for r in agent.reasons[:4]])
        if score >= 80:
            level = "critical"
        elif score >= 60:
            level = "high"

    executive_designations = [x for x in (executive_designations or []) if x]
    if executive_recipient_count:
        # Executive presence alone is a risk signal; context still controls the
        # final priority so a routine FYI is not automatically critical.
        context_risk = level in {"medium", "high", "critical"} or sentiment in {"negative", "concerned"}
        score = min(100, score + (30 if context_risk else 12))
        reasons.append(
            "executive recipient: "
            + ", ".join(executive_designations[:3] or [f"{executive_recipient_count} configured executive(s)"])
        )
        if score >= 80:
            level = "critical"
        elif score >= 60:
            level = "high"
        elif score >= 35:
            level = "medium"

    severity, severity_reasons = calculate_severity(
        text,
        sender_type,
        sentiment,
        tat_breached=tat_breached,
        response_minutes=response_minutes,
        tat_minutes=tat_minutes,
    )

    # If the classifier identifies an actual outage/service-down condition,
    # severity is critical regardless of sender domain. A planned downtime
    # question remains high/concerned rather than being falsely called an outage.
    if actual_outage_context:
        severity = "critical"
        severity_reasons.append("customer-impacting outage/service unavailability")
    elif issue_type == "outage" and downtime_risk_context:
        severity = "high"
        severity_reasons.append("planned downtime/outage-window risk")

    if executive_recipient_count and level in {"high", "critical"} and severity in {"normal", "medium"}:
        severity = "high"
        severity_reasons.append("executive recipient with escalation context")

    if sender_type == "customer":
        customer_waiting = True
        engineer_action_required = True
    elif sender_type in {"engineer", "internal"}:
        customer_waiting = False
        engineer_action_required = False
    else:
        customer_waiting = True
        engineer_action_required = True

    if response_minutes is not None:
        customer_waiting = False

    summary_parts = [
        f"{sender_type.title()} email",
        f"Product: {product}",
        f"Issue: {issue_type.replace('_', ' ')}",
        f"Sentiment: {sentiment}",
        f"Emotion: {agent.emotion}",
        f"Customer requirement: {agent.customer_requirement}",
        f"Local AI confidence: {agent.confidence}%",
        f"Priority: {severity}",
    ]
    if severity_reasons:
        summary_parts.append(f"Priority signals: {len(severity_reasons)}")
    if reasons:
        summary_parts.append(f"Escalation signals: {len(reasons)}")

    action = "Review and classify."
    if severity == "critical":
        action = "Immediate engineer/lead action required. Prioritize customer response now."
    elif severity == "high":
        action = ("High-priority customer request. Assign an engineer and respond as soon as possible."
                  if sender_type == "customer"
                  else "High-priority operational event. Review ownership/customer impact and act promptly.")
    elif level in {"critical", "high"}:
        action = "Engineer/lead review recommended immediately."
    elif tat_breached:
        action = "Response is overdue; assign or follow up now."
    elif sender_type == "customer":
        action = "Track for engineer response and TAT."
    elif sender_type in {"engineer", "internal"}:
        action = "Treat as internal coordination unless customer impact is present."

    # Explicit monitoring decision. This is deliberately separate from
    # engineer_action_required so courtesy/FYI mail can remain visible without
    # polluting workload and TAT metrics.
    response_required, exclusion_reason = detect_response_requirement(
        subject, body, sender_type=sender_type, sentiment=sentiment,
        email_category=email_category, executive_context=executive_context,
    )
    monitoring_excluded = not response_required
    if monitoring_excluded:
        customer_waiting = False
        engineer_action_required = False

    # Keep the reason visible in the Events/Email intelligence context.
    all_reasons = severity_reasons + reasons
    if exclusion_reason:
        all_reasons.append(f"not tracked: {exclusion_reason}")

    return {
        "sender_domain": email_domain(sender_email),
        "sender_type": sender_type,
        "severity": severity,
        "sentiment": sentiment,
        "escalation_score": score,
        "escalation_level": level,
        "escalation_reasons": " | ".join(all_reasons),
        "customer_waiting": customer_waiting,
        "engineer_action_required": engineer_action_required,
        "response_required": response_required,
        "monitoring_excluded": monitoring_excluded,
        "exclusion_reason": exclusion_reason,
        "product": product,
        "issue_type": issue_type,
        "email_category": email_category,
        "ai_summary": ". ".join(summary_parts) + ".",
        "recommended_action": action,
    }
