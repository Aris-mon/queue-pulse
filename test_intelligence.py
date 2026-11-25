from intelligence import build_intelligence


def test_customer_normal_email():
    x = build_intelligence(
        "Status update",
        "Could you please provide an update?",
        "customer@example.com",
        "customer",
        tat_breached=False,
        recipient_count=2,
    )
    assert x["sender_type"] == "customer"
    assert x["severity"] in {"normal", "medium"}


def test_quick_response_request_is_high():
    x = build_intelligence(
        "Support request",
        "Could we discuss this further as soon as possible? I need a quick response.",
        "customer@example.com",
        "customer",
        tat_breached=False,
        tat_minutes=10,
        recipient_count=2,
    )
    assert x["severity"] == "high"


def test_explicit_escalation():
    x = build_intelligence(
        "Urgent escalation",
        "This production outage is unacceptable. Please escalate to management.",
        "customer@example.com",
        "customer",
        tat_breached=True,
        recipient_count=8,
    )
    assert x["severity"] == "critical"
    assert x["escalation_score"] >= 80
    assert x["escalation_level"] == "critical"


def test_blocked_customer_is_high():
    x = build_intelligence(
        "Unable to proceed",
        "The customer is blocked and waiting for your response. Please respond urgently.",
        "customer@example.com",
        "customer",
        tat_breached=False,
        recipient_count=3,
    )
    assert x["severity"] == "high"


def test_normal_question_stays_normal():
    x = build_intelligence(
        "Question",
        "Could you please confirm whether this configuration is supported?",
        "customer@example.com",
        "customer",
        tat_breached=False,
        recipient_count=2,
    )
    assert x["severity"] == "normal"


def test_customer_substantive_appreciation_is_recognition():
    x = build_intelligence(
        "Successful Installation - Appreciation",
        "The installation was completed successfully. Thank you for the excellent support and great work by your engineer.",
        "customer@example.com",
        "customer",
        recipient_count=2,
    )
    assert x["sentiment"] == "positive"
    assert x["email_category"] == "recognition"


def test_customer_thanks_only_is_not_recognition():
    x = build_intelligence(
        "RE: Installation",
        "Thank you.",
        "customer@example.com",
        "customer",
        recipient_count=2,
    )
    assert x["email_category"] != "recognition"


def test_customer_courtesy_update_is_not_recognition():
    x = build_intelligence(
        "RE: Schedule",
        "Thanks for the update. We will review and get back to you.",
        "customer@example.com",
        "customer",
        recipient_count=2,
    )
    assert x["email_category"] != "recognition"


def test_recognition_wins_over_old_quoted_negative_thread():
    x = build_intelligence(
        "RE: Appreciation",
        "Thank you for the excellent support.\n\nFrom: customer@example.com\nSent: yesterday\nThis is unacceptable and we are very frustrated.",
        "customer@example.com",
        "customer",
        recipient_count=2,
    )
    assert x["sentiment"] == "positive"
    assert x["email_category"] == "recognition"


def test_technical_critical_is_not_negative_sentiment():
    x = build_intelligence(
        "Critical installation issue",
        "Please help with the installation. The service is critical to production.",
        "customer@example.com",
        "customer",
        recipient_count=2,
    )
    assert x["sentiment"] in {"neutral", "concerned"}
    assert x["sentiment"] != "negative"


def test_sentiment_courtesy_and_technical_neutrality():
    from local_ai_agent import analyze_email
    assert analyze_email("Update", "Thank you for the update.").sentiment == "neutral"
    assert analyze_email("Issue", "We have an issue with the storage configuration. Please help.").sentiment == "neutral"
    assert analyze_email("Completion", "The issue has been resolved.").sentiment == "neutral"


def test_sentiment_context():
    from local_ai_agent import analyze_email
    assert analyze_email("Support", "We are very pleased with the excellent support during the installation.").sentiment == "positive"
    assert analyze_email("Delay", "We are still waiting for the engineer and this is causing business impact.").sentiment in {"concerned", "negative"}
    assert analyze_email("Complaint", "This is unacceptable and we are extremely disappointed with the delay.").sentiment == "negative"


def test_recognition_not_courtesy():
    from local_ai_agent import analyze_email
    assert not analyze_email("", "Thank you.").recognition
    assert not analyze_email("", "Much appreciated.").recognition
    assert analyze_email("", "Thank you for the excellent support. The installation was completed successfully.").recognition


def test_internal_missed_customer_call_is_critical_event():
    x = build_intelligence(
        "Customer call",
        "No one joined from your end, so we are ending the meeting. Please let us know a convenient time to connect for further troubleshooting.",
        "system@hpe.com",
        "internal",
        recipient_count=2,
    )
    assert x["severity"] == "critical"
    assert x["response_required"] is False


def test_engineer_coordination_is_not_customer_workload():
    x = build_intelligence(
        "Schedule confirmation",
        "Please confirm the slot and share the details with the team.",
        "engineer@hpe.com",
        "engineer",
        recipient_count=2,
    )
    assert x["response_required"] is False


def test_technical_success_is_not_positive_sentiment():
    from local_ai_agent import analyze_email
    assert analyze_email("Resolved", "The issue has been resolved and service is available now.").sentiment == "neutral"


def test_courtesy_is_not_positive_sentiment():
    from local_ai_agent import analyze_email
    assert analyze_email("Update", "Thank you for the update.").sentiment == "neutral"


def test_customer_frustration_is_negative():
    from local_ai_agent import analyze_email
    assert analyze_email("Complaint", "We are extremely disappointed with the repeated delays and poor support.").sentiment == "negative"


def test_escalation_tool_waiting_call_is_critical_and_concerned():
    x = build_intelligence(
        "Escalation Tool",
        "I need immediate support on this ASAP. We are waiting on the call, and nobody has joined so far. Kindly expedite this on priority.",
        "stakeholder@hpe.com",
        "internal",
        tat_breached=False,
        tat_minutes=60,
        recipient_count=2,
    )
    assert x["email_category"] == "escalation"
    assert x["sentiment"] == "concerned"
    assert x["severity"] == "critical"
    assert x["response_required"] is True


def test_schedule_request_with_thanks_is_not_recognition():
    x = build_intelligence(
        "Installation schedule",
        "Thanks for the update. Please confirm the installation schedule for tomorrow.",
        "customer@example.com",
        "customer",
        recipient_count=2,
    )
    assert x["email_category"] == "schedule_request"
    assert x["sentiment"] in {"neutral", "concerned"}
