"""Fixed daily editions. Missing evidence never becomes a fresh, eligible opening."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .domain import classify, EARLY, SENIOR, fingerprint, plain

POLICY_VERSION = 2
CAP = 100


def timestamp(value):
    """Require an actual instant; a calendar date or naive time cannot prove 24h."""
    if not isinstance(value, str) or 'T' not in value:
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError,OverflowError):
        return None


def cutoff_for(current, hour, timezone_name, minute=0):
    local = current.astimezone(ZoneInfo(timezone_name))
    cutoff = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local < cutoff:
        cutoff -= timedelta(days=1)
    return cutoff.astimezone(timezone.utc)


def next_cutoff(current, hour, timezone_name, minute=0):
    local = current.astimezone(ZoneInfo(timezone_name))
    cutoff = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local >= cutoff:
        cutoff += timedelta(days=1)
    return cutoff.astimezone(timezone.utc)


def identity(job):
    # The same role/location on two boards, or a changed URL, must not make a new edition.
    normalize = lambda x: re.sub(r'[^a-z0-9]', '', (x or '').lower())
    return fingerprint('|'.join(normalize(job.get(k)) for k in ('company', 'title', 'location')))


def eligibility(job, start_date='2027-06-01', strict_cohort=True):
    title, description = job['title'], plain(job.get('description', ''))
    result = classify(title, description, job.get('location', ''), job.get('country', ''), start_date)
    reasons = list(result['scope_reasons']) if not result['in_scope'] else []
    evidence = []
    if not job.get('verified_at') or not description.strip():
        reasons.append('Full employer posting has not been verified')
    if result['sponsorship'] in {'restricted', 'conflict'} or job.get('sponsorship') in {'restricted', 'conflict'}:
        reasons.append('Work authorization or future sponsorship is restricted or conflicting')
    if SENIOR.search(title):
        reasons.append('Senior title cannot qualify through an Engineer I or Associate suffix')
    if re.search(r'\b(?:grad\w*|entry[ -]level|early[ -]career)\b[^\n]{0,80}\b202[89]\b|\b202[89]\b[^\n]{0,80}\bgrad\w*\b', title, re.I) and not re.search(r'\b2027\b', title):
        reasons.append('Explicit graduate cohort is later than 2027')
    if re.search(r'\b(?:internship|intern|co-op|part[ -]?time|contractor|contract|temporary)\b', title, re.I) or re.sub('[^a-z]','',job.get('employment_type', '').lower()) in {'intern', 'internship', 'parttime', 'contract', 'contractor', 'temporary','fixedterm'}:
        reasons.append('Not a permanent full-time entry-level opening')
    if re.search(r'\b(?:contract|temporary|part[ -]?time)\s+(?:role|position|opening|opportunity)\b',description,re.I):
        reasons.append('Posting describes a contract, temporary or part-time position')
    if re.search(r'\b(?:senior|mid[ -]?(?:level|senior)|intermediate|experienced|expert|executive|manager)\b',job.get('employer_level',''),re.I):
        reasons.append('Employer labels this above entry level')
    if re.search(r'\b(?:ph\.?d\.?|doctoral|postdoc|masters?[- ]only)\b', title, re.I):
        reasons.append('Role targets an advanced degree rather than a bachelor graduate')
    for sentence in re.split(r'[;\n]|(?<=[.!?])\s+', description):
        if (re.search(r"(?:\b(?:require[sd]?|must (?:have|hold|possess))\b[^.;]{0,60}\b(?:master'?s?|ph\.?d\.?|doctoral)|\b(?:master'?s?|ph\.?d\.?|doctoral)\b[^.;]{0,50}\b(?:required|only)\b)",sentence,re.I)
            and not re.search(r"\b(?:bachelor'?s?|undergraduate|B\.?S\.?)\b",sentence,re.I)):
            reasons.append('Required advanced degree has no stated bachelor-degree path')

    # Associate/Engineer I are weak hints, not permission to ignore required experience.
    text = description
    text=re.sub(r'\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s*\((\d{1,2})\)',r'\1',text,flags=re.I)
    for word, number in [('zero', '0'), ('one', '1'), ('two', '2'), ('three', '3'), ('four', '4'), ('five', '5'), ('six', '6'), ('seven', '7'), ('eight', '8'), ('ten', '10')]:
        text = re.sub(r'\b' + word + r'(?=\s+(?:to\s+\w+\s+)?years?\b)', number, text, flags=re.I)
    requirements = []
    for sentence in re.split(r'[;\n]|(?<=[.!?])\s+', text):
        if not re.search(r'preferred|nice.to.have|bonus|desirable|not required',sentence,re.I):
            for match in re.finditer(r'\b(\d{1,2})\+?\s+years?\s+(?:with|and)\s+(?:a\s+)?(?:bachelor|master|ph\.?d|doctoral)',sentence,re.I):
                requirements.append((int(match.group(1)),sentence.strip()))
        for match in re.finditer(r"\b(\d{1,2})\s*(?:(?:[-–]|to)\s*(\d{1,2})|\+|or more|or greater|plus)?\s*(?:years?|yrs?)[’']?\s+(?:of\s+)?(?:[^.;\n]{0,90}?\b)?(?:experience|building|developing|designing|shipping|working)\b", sentence, re.I):
            context=sentence[max(0,match.start()-40):match.end()+35]
            if re.search(r'preferred|nice.to.have|bonus|desirable|not required', context, re.I) and not re.search(r'\b(?:required|must|minimum|at least)\b',context.replace('not required',''),re.I):
                continue
            upper_bound=re.search(r'\b(?:up to|less than|at most|no more than|fewer than|maximum(?: of)?)\s*$',sentence[max(0,match.start()-25):match.start()],re.I)
            requirements.append((0 if upper_bound else int(match.group(1)), sentence.strip()))
    # Hold alternatives for review rather than choosing a low requirement that needs a Masters/PhD.
    if any(years > 0 for years, _ in requirements):
        reasons.append('Posting requires prior experience; not confirmed open to a zero-experience graduate')
    elif requirements:
        evidence.append(next(sentence for years, sentence in requirements if years == 0))
    explicit = bool(EARLY.search(title))
    if not explicit and not any(years == 0 for years, _ in requirements) and not re.search(r'\b(?:no (?:prior |professional )?experience (?:is )?(?:required|necessary)|recent graduates? (?:are )?(?:welcome|encouraged))\b', text, re.I):
        reasons.append('No explicit new-grad or zero-experience eligibility evidence')
    elif explicit:
        evidence.append(title)

    # A year elsewhere in boilerplate is not a graduation requirement.
    cohort = bool(re.search(r'\b2027\b', title) and re.search(r'\b(?:grad(?:uate|uation|uates)?|entry[ -]level|early[ -]career|start|university|campus)\b', title, re.I))
    for sentence in re.split(r'[;\n]|(?<=[.!?])\s+', description):
        if re.search(r'\b2027\b', sentence) and re.search(r'\b(?:graduat(?:e|es|ing|ion)|degree.*complet|start(?:ing| date)?|join(?:ing)? date)\b', sentence, re.I):
            cohort = True
            evidence.append(sentence.strip()[:600])
        if re.search(r'\b(?:graduat(?:e|es|ing|ion)|degree.*complet)\b',sentence,re.I):
            years=re.findall(r'\b202[0-9]\b',sentence)
            if years and '2027' not in years:
                reasons.append('Explicit graduation-year requirement does not include 2027')
    availability = datetime.fromisoformat(start_date).date()
    if re.search(r'\b(?:immediate start|start immediately|start as soon as possible|must be available immediately)\b', description, re.I):
        reasons.append('Posting requires an immediate start before availability')
    months = {name[:3].lower(): i for i, name in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'], 1)}
    month_pattern=r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    for match in re.finditer(r'\b(?:start(?:ing| date| by)?|commenc(?:e|ing)|join(?:ing)? date)\s*(?:is |: |in |on |by )*('+month_pattern+r')\s+(?:(\d{1,2}),?\s+)?(20\d\d)', description, re.I):
        try:date = datetime(int(match.group(3)), months[match.group(1)[:3].lower()], int(match.group(2) or 1)).date()
        except ValueError:
            reasons.append('Stated start date is invalid and needs review');continue
        if date < availability:
            reasons.append('Stated start date is before June 2027 availability')
        else:
            cohort = True
            evidence.append(match.group(0))
    if re.search(r'\b(?:graduat\w*|degree[^.]{0,35})[^.]{0,50}\b(?:by|before|no later than)\s+(?:January|February|March|April|May)\s+(?:\d{1,2},?\s+)?2027\b', description, re.I):
        reasons.append('Graduation deadline precedes June 2027')
    if strict_cohort and not cohort:
        reasons.append('June 2027 cohort or compatible start date is unconfirmed')
    return {'eligible': not reasons, 'reasons': list(dict.fromkeys(reasons)), 'evidence': list(dict.fromkeys(evidence)), 'cohort_confirmed': cohort}
