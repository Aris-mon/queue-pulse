"""PDL email intelligence — context-first deterministic guardrail.

Separates customer attitude from operational vocabulary, urgency and recognition.
The Ollama provider can enrich this result when available.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Iterable

@dataclass(frozen=True)
class AgentResult:
    sentiment: str
    emotion: str
    customer_requirement: str
    escalation_risk: bool
    confidence: int
    reasons: tuple[str, ...]
    recognition: bool = False

_PHRASE_GROUPS = {
    "anger": ("angry","furious","unacceptable","ridiculous","terrible","very disappointed","extremely disappointed","poor support","not happy","not satisfied","dissatisfied","fed up","frustrated","disappointed","upset","worst","disappointing","very unhappy","extremely unhappy","bad experience","poor experience","let down","failed us","not acceptable","waste of time","unprofessional"),
     "concern": ("concerned","worry","worried","still waiting","waiting on the call","waiting on call","waiting for the engineer","no response","no update","delayed","delay in","overdue","when can you","how long will","business impact","customer impact","at risk","risk to","blocking us","customer is blocked","we are blocked","unfortunately","cannot proceed","unable to proceed","no one joined","nobody joined","no one has joined","nobody has joined","did not join","didn't join","missed the call"),
    "urgency": ("urgent","urgently","asap","immediately","right away","as soon as possible","time sensitive","today","by eod","before go-live","before production"),
    "escalation": ("escalate","escalation","raise this","raise to management","raise with management","take this to management","management escalation","director escalation","executive escalation","formal complaint","complaint","senior management"),
    "positive": ("excellent work","excellent job","great work","great job","outstanding work","outstanding job","exceptional work","well done","kudos","top talent","valuable contribution","strong contribution","well deserved","great support","excellent support","outstanding support","excellent service","pleased with the work","very pleased with","extremely pleased with","happy with the outcome","happy with the service","satisfied with the support","satisfied with the work","professionalism","fantastic work","brilliant work"),
}

_REQUIREMENTS = (
    ("schedule / meeting request", ("schedule","meeting","call","availability","calendar","join the call")),
    ("status / progress update", ("any update","status update","current status","progress","latest status","please update")),
    ("technical assistance", ("help","support","issue","problem","error","failed","failure","not working","unable to")),
    ("immediate restoration / action", ("production down","service down","outage","restore","immediately","urgent","asap")),
    ("management escalation", ("escalate","escalation","manager","management","leadership","complaint")),
    ("confirmation / clarification", ("confirm","clarify","clarification","question","query","information")),
)
_NEGATION_PATTERNS = (r"\bnot\s+(?:at\s+all\s+)?happy\b",r"\bnot\s+satisfied\b",r"\bnot\s+acceptable\b",r"\bno\s+(?:response|update|progress)\b",r"\bwithout\s+(?:a\s+)?response\b",r"\bnever\s+(?:received|heard|got)\b")

def _normalize(value: str | None) -> str:
    return re.sub(r"\s+"," ",str(value or "")).strip().lower()


def _strip_signature(text: str) -> str:
    raw=str(text or "").replace("\r\n","\n").replace("\r","\n")
    out=[]
    signature_inline = re.compile(
        r"(?:for\s+(?:(?:any|all)\s+)?)?escalations?\s*[,;:-]?\s*(?:please\s+)?contact\b",
        re.I,
    )
    for line in raw.split("\n"):
        low=line.strip().lower()
        if re.match(r"^(for any|for all)\s+escalations?\b.*\bplease\s+contact\b", low): break
        if re.match(r"^(for any|for all)\s+escalations?\b.*\bcontact\b", low): break
        if re.match(r"^escalations?\s*[:|-]", low) and len(out)>=2: break
        if low in {"regards","best regards","kind regards","thanks & regards","thank you & regards","warm regards","best","thanks"} and len(out)>=2: break
        inline=signature_inline.search(line)
        if inline:
            prefix=line[:inline.start()].rstrip(" -:;|")
            if prefix: out.append(prefix)
            break
        out.append(line)
    cleaned="\n".join(out).strip()
    cleaned=re.split(r"(?i)(?:for\s+(?:(?:any|all)\s+)?)?escalations?\s*[,;:-]?\s*(?:please\s+)?contact\b.*", cleaned, maxsplit=1)[0].strip()
    return cleaned

def _matches(text: str, phrases: Iterable[str]) -> list[str]:
    return [p for p in phrases if p in text]

def _latest_message(text: str) -> str:
    raw=str(text or "").replace("\r","")
    kept=[]
    for line in raw.split("\n"):
        stripped=line.strip(); low=stripped.lower()
        if re.match(r"^on .+ wrote:$",low) or stripped.startswith(">") or low.startswith("-----original message") or low.startswith("begin forwarded message"):
            break
        if re.match(r"^(from|sent|to|cc|subject):",low) and kept:
            break
        kept.append(line)
    return _strip_signature("\n".join(kept)).strip() or _strip_signature(raw).strip()

def _is_recognition(subject_text: str, body_text: str) -> tuple[bool,list[str]]:
    current=f"{_normalize(subject_text)} {_normalize(body_text)}".strip()
    if current in {"thank you","thanks","thanks a lot","many thanks","appreciate it","much appreciated","noted","acknowledged","received with thanks","well received"}:
        return False,[]
    strong=("excellent work","excellent job","great work","great job","outstanding work","outstanding job","exceptional work","well done","kudos","top talent","valuable contribution","strong contribution","well deserved","commend","commended","recognition","recognize","recognised","recognized","special thanks to","special thanks for","fantastic work","brilliant work","superb work","great support","excellent support","outstanding support","excellent service","pleased with the work","very pleased with","extremely pleased with","satisfied with the work","satisfied with the support","happy with the outcome","successful completion","successfully completed","completed successfully","thank you for your excellent","thank you for the great","thank you for the outstanding")
    hits=[p for p in strong if p in current]
    objects=("work","job","support","service","effort","help","contribution","team","engineer","engineers","installation","deployment","upgrade","migration","project","activity","assistance","commitment","dedication","professionalism","response","handling","resolution","completion","outcome","delivery","performance")
    has_object=any(x in current for x in objects)
    completion=any(x in current for x in ("successfully completed","successful completion","completed successfully","installation completed","deployment completed","upgrade completed","migration completed","activity completed","work completed","project completed","issue resolved","issue has been resolved","problem resolved","go-live completed","delivered successfully"))
    blocking=any(x in current for x in ("still waiting","no response","no update","unacceptable","escalate","escalation","complaint","frustrated","disappointed","urgent","critical","blocked","production down","service down","not working","unable to","delay","delayed"))
    if blocking:
        return False,hits
    courtesy_praise=any(x in current for x in ("thank you for the excellent","thank you for the great","thank you for the outstanding","thank you for your excellent","thank you for your great","thank you for your outstanding","really appreciate the excellent","greatly appreciate the work","greatly appreciate your support"))
    # A completion/finalization statement is NOT recognition by itself. Recognition
    # requires explicit appreciation/praise of the work/service/team/person. A
    # schedule or project email may legitimately contain "successfully completed"
    # as a factual statement without being a recognition email.
    explicit_appreciation = any(x in current for x in (
        "thank you", "thanks", "appreciate", "appreciation", "kudos", "commend",
        "praise", "recognition", "well done", "great job", "great work",
        "excellent job", "excellent work", "outstanding job", "outstanding work",
        "exceptional work", "fantastic work", "brilliant work",
    ))
    recognition=bool(
        courtesy_praise
        or (hits and has_object and explicit_appreciation)
        or (completion and has_object and explicit_appreciation)
    )
    return recognition,list(dict.fromkeys(hits))

def _sentence_sentiment(text: str) -> tuple[float,float,list[str],list[str]]:
    negative=positive=0.0; neg_ev=[]; pos_ev=[]
    sentences=re.split(r"(?<=[.!?])\s+|\n+",text)
    neg_phrases=("very disappointed","extremely disappointed","disappointed","frustrated","angry","furious","unacceptable","not acceptable","not happy","not satisfied","dissatisfied","unhappy","upset","terrible","awful","poor support","poor experience","bad experience","let down","failed us","unprofessional","waste of time")
    pos_phrases=("excellent work","excellent job","great work","great job","outstanding work","outstanding job","exceptional work","well done","kudos","top talent","valuable contribution","strong contribution","well deserved","great support","excellent support","outstanding support","excellent service","pleased with the work","very pleased with","extremely pleased with","happy with the outcome","happy with the service","satisfied with the support","satisfied with the work","fantastic work","brilliant work","professionalism","greatly appreciate the work")
    for sentence in sentences:
        s=_normalize(sentence)
        if not s: continue
        clauses=re.split(r"\b(?:but|however|although|though)\b",s)
        for clause in clauses:
            ns=[p for p in neg_phrases if p in clause]; ps=[p for p in pos_phrases if p in clause]
            if ns: negative += len(ns)*2.5; neg_ev.extend(ns[:3])
            if ps: positive += len(ps)*2.5; pos_ev.extend(ps[:3])
        if re.search(r"\b(?:not|never)\s+(?:happy|pleased|satisfied|impressed)\b",s):
            negative+=3.5; neg_ev.append("negated positive sentiment")
    return negative,positive,list(dict.fromkeys(neg_ev)),list(dict.fromkeys(pos_ev))

def analyze_email(subject: str | None, body: str | None) -> AgentResult:
    subject_text=_normalize(subject); body_text=_normalize(_latest_message(str(body or ""))); text=f"{subject_text} {body_text}".strip()
    recognition,recognition_hits=_is_recognition(subject_text,body_text)
    # Courtesy-only messages are neutral. This guard runs before broader
    # phrase scoring so "thank you", "thanks for the update", etc. cannot be
    # promoted to positive sentiment merely by courtesy vocabulary.
    courtesy_only = bool(re.fullmatch(r"(?:hi|hello|dear [a-z .'-]+,?\s*)?(?:thank you|thanks|thanks a lot|many thanks|much appreciated|appreciate it|appreciated|noted|acknowledged|received with thanks)[.!\s]*", text, flags=re.I))
    hits={name:_matches(text,phrases) for name,phrases in _PHRASE_GROUPS.items()}
    negation_hits=[p for p in _NEGATION_PATTERNS if re.search(p,text,flags=re.I)]
    negative_score,positive_score,neg_ev,pos_ev=_sentence_sentiment(text)
    concern_score=len(hits["concern"])*2.0; anger_score=len(hits["anger"])*4.0; escalation_score=len(hits["escalation"])*4.0
    operational_concern = any(x in text for x in (
        "production is down", "production down", "service is down",
        "service unavailable", "system is down", "complete outage",
        "major outage", "production outage", "downtime required", "downtime is required",
        "required downtime", "will there be downtime",
        "is downtime required", "maintenance window", "service interruption",
    ))
    if courtesy_only and not recognition:
        sentiment,emotion="neutral","neutral"
    elif recognition: sentiment,emotion="positive","appreciative"
    elif anger_score+negative_score>=5.0 or negation_hits: sentiment,emotion="negative","frustrated" if hits["anger"] else "negative"
    elif positive_score>=4.5 and positive_score>(negative_score+1.5): sentiment,emotion="positive","positive"
    elif concern_score>=4.0 or (negative_score>=2.5 and negative_score>positive_score) or operational_concern:
        sentiment,emotion="concerned","operational concern" if operational_concern else "concerned"
    else: sentiment,emotion="neutral","neutral"
    requirement="general review"; requirement_score=0
    for label,phrases in _REQUIREMENTS:
        score=sum(2 if p in subject_text else 1 for p in phrases if p in text)
        if score>requirement_score: requirement_score=score; requirement=label
    escalation_risk=bool(escalation_score>=4 or (sentiment=="negative" and negative_score>=5) or ("urgent" in text and any(x in text for x in ("blocked","production","business impact","customer impact","missed call","did not join","failed to attend"))) or any(x in text for x in ("production down","service down","customer blocked","did not join the call","didn't join the call","missed the call","no one joined","nobody joined","engineer did not join","engineer didn't join","failed to attend the call")))
    reasons=[]
    if recognition_hits: reasons.append(f"recognition: {', '.join(recognition_hits[:5])}")
    if neg_ev: reasons.append(f"negative evidence: {', '.join(neg_ev[:4])}")
    if pos_ev: reasons.append(f"positive evidence: {', '.join(pos_ev[:4])}")
    for label in ("concern","urgency","escalation"):
        if hits[label]: reasons.append(f"{label}: {', '.join(hits[label][:3])}")
    if negation_hits: reasons.append("negative/unsatisfied wording detected")
    if requirement!="general review": reasons.append(f"requirement: {requirement}")
    evidence=len(neg_ev)+len(pos_ev)+min(3,len(hits["concern"]))+min(2,len(hits["escalation"]))
    if sentiment=="neutral" and evidence==0: confidence=60
    elif sentiment in {"positive","negative"}: confidence=min(96,72+evidence*5)
    elif sentiment=="concerned": confidence=min(92,68+evidence*5)
    else: confidence=min(90,65+evidence*4)
    return AgentResult(sentiment,emotion,requirement,escalation_risk,confidence,tuple(list(dict.fromkeys(reasons))[:10]),recognition)
