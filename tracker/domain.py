"""Conservative, inspectable rules. No resume matching or inferred visa approval."""
import hashlib
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]
        self.skipping=0

    def handle_starttag(self, tag, attrs):
        if tag in {'script','style'}: self.skipping+=1

    def handle_endtag(self, tag):
        if tag in {'script','style'}: self.skipping=max(0,self.skipping-1)

    def handle_data(self, data):
        if not self.skipping and data.strip(): self.parts.append(data.strip())


def plain(value):
    if not value or '<' not in value:
        return value or ''
    parser=_Text()
    parser.feed(value)
    return ' '.join(parser.parts)


def canonical_url(value):
    p = urlsplit(value.strip())
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('An HTTP(S) application link is required')
    # Keep job identity parameters; remove advertising and referral parameters only.
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith(('utm_',)) and k.lower() not in
             {'source', 'ref', 'referral', 'gh_src', 'lever-source', 'lever-origin', 'src', 'refid', 'trackingid'}]
    host = p.hostname.lower()
    if host in ('boards.greenhouse.io', 'job-boards.greenhouse.io'):
        host = 'job-boards.greenhouse.io'
    path = p.path.rstrip('/')
    if host in {'jobs.ashbyhq.com','jobs.lever.co'}:
        path = re.sub(r'/(?:apply|application)$','',path)
    if re.fullmatch(r'/o/[^/]+/c/new',path):
        path=path[:-6]
    return urlunsplit(('https' if p.scheme == 'https' else 'http', host, path, urlencode(sorted(query)), ''))


def fingerprint(text):
    return hashlib.sha256(text.encode()).hexdigest()


NEGATIVE = [
    r'\b(?:u\.?s\.?|united states)\s+person\s+(?:is\s+)?required\b',
    r'\bmust\s+be\s+(?:a\s+)?(?:u\.?s\.?|united states)\s+person\b(?![^.!?]{0,60}\b(?:or|export license)\b)',
    r'\b(?:must|shall|need to)\s+(?:be\s+)?(?:a\s+)?(?:u\.?s\.?|united states)\s+citizen\b',
    r'\b(?:u\.?s\.?|united states)\s+citizenship\s+(?:is\s+)?required\b',
    r'\b(?:u\.?s\.?|united states)\s+citizens?\s+only\b',
    r'\b(?:only|exclusively)\s+(?:open|available)\s+to\s+(?:u\.?s\.?|united states)\s+citizens\b',
    r'\b(?:citizens|citizenship)\s+(?:or|and)\s+(?:permanent residents|permanent residency)\s+only\b',
    r'\b(?:must|need to)\s+be\s+(?:a\s+)?(?:u\.?s\.?\s+)?(?:citizen\s+or\s+)?(?:permanent resident|green card holder)\b',
    r'\b(?:cannot|can not|unable to|will not|do not|does not|not able to)\s+(?:provide\s+|offer\s+)?(?:(?:visa|immigration|H[ -]?1B)\s+)?sponsor(?:ship)?\b',
    r'\bno\s+(?:(?:visa|immigration|employment|H[ -]?1B)\s+)?sponsorship\b',
    r'\bsponsorship\s+(?:is\s+)?(?:not\s+(?:available|offered|provided)|unavailable)\b',
    r'\bwithout\s+(?:the\s+)?(?:need\s+for\s+)?(?:employment\s+|visa\s+|immigration\s+)?sponsorship\b[^.!?]{0,80}\b(?:future|ever)\b',
    r'\b(?:not\s+(?:require|need)|never\s+(?:require|need))\b[^.!?]{0,80}\bsponsorship\b',
    r'\b(?:OPT|CPT|F-1)\s+(?:candidates?\s+)?(?:are\s+)?not\s+(?:accepted|eligible|supported)\b',
]
POSITIVE = [
    r'\b(?:visa|immigration)\s+sponsorship\s+(?:is\s+)?(?:available|offered|provided)\b',
    r'\b(?:we|company|employer)\s+(?:will|can|does)\s+(?:provide\s+|offer\s+)?(?:(?:visa|immigration|H[ -]?1B)\s+)?sponsor(?:ship)?\b',
    r'\b(?:offer|provide|offers|provides)\s+(?:visa|immigration)\s+sponsorship\b',
]
ROLE = re.compile(r'\b(software|developer|swe|sde|backend|back.end|frontend|front.end|full.?stack|devops|sre|site reliability|data engineer|machine learning|ml engineer|ai engineer|cloud engineer|platform engineer|infrastructure engineer|production engineer|release engineer|forward.deployed engineer|security engineer|test engineer|quality engineer|qa engineer|automation engineer|systems engineer|embedded|firmware|quantitative developer|research engineer|application engineer)\b', re.I)
EARLY = re.compile(r'\b(new[ -]?(?:college[ -]?)?grad(?:uate)?s?|entry[ -]?level|early[ -]?career|university grad(?:uate)?|graduate|junior|associate|engineer\s*(?:i|1))\b', re.I)
SENIOR = re.compile(r'\b(senior|sr\.?|staff|principal|director|lead|manager|architect|engineer\s*(?:iii|iv|[4-9]))\b', re.I)
US_STATES = {'alabama','alaska','arizona','arkansas','california','colorado','connecticut','delaware','florida','georgia','hawaii','idaho','illinois','indiana','iowa','kansas','kentucky','louisiana','maine','maryland','massachusetts','michigan','minnesota','mississippi','missouri','montana','nebraska','nevada','new hampshire','new jersey','new mexico','new york','north carolina','north dakota','ohio','oklahoma','oregon','pennsylvania','rhode island','south carolina','south dakota','tennessee','texas','utah','vermont','virginia','washington','west virginia','wisconsin','wyoming'}
US_CODES = 'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split()
US_CITIES = {'san francisco','san jose','seattle','new york','boston','philadelphia','austin','chicago','palo alto','mountain view','sunnyvale','los angeles','san diego','santa clara','denver','atlanta','pittsburgh','washington dc','redmond','bellevue','menlo park','irvine'}


US_TOKEN = re.compile(r'(?<!\w)(?:united states(?: of america)?|usa|u\.s\.?|us)(?!\w)',re.I)
US_PLACE = re.compile(r'\b(?:'+ '|'.join(re.escape(x) for x in sorted((US_STATES-{'georgia'})|US_CITIES, key=len, reverse=True))+r')\b',re.I)
US_STATE_CODE = re.compile(r',\s*(?:'+ '|'.join(US_CODES) +r')(?:\b|\s|$)')
FOREIGN = re.compile(r'\b(?:united arab emirates|uae|u\.a\.e\.?|dubai|abu dhabi|sharjah|qatar|doha|saudi arabia|riyadh|kuwait|bahrain|oman|muscat|egypt|cairo|south africa|nigeria|kenya|morocco|canada|toronto|vancouver|montreal|ottawa|united kingdom|uk|england|scotland|london|india|bangalore|bengaluru|hyderabad|pune|chennai|mumbai|delhi|germany|berlin|munich|frankfurt|singapore|australia|sydney|melbourne|ireland|dublin|israel|tel aviv|poland|warsaw|france|paris|netherlands|amsterdam|mexico|switzerland|zurich|lausanne|japan|tokyo|south korea|seoul|taiwan|taipei|china|beijing|shanghai|hong kong|spain|madrid|barcelona|portugal|lisbon|sweden|stockholm|finland|helsinki|denmark|copenhagen|norway|oslo|brazil|sao paulo|argentina|chile|colombia|peru|pakistan|bangladesh|vietnam|thailand|malaysia|indonesia|philippines|new zealand|armenia|serbia|kazakhstan|tbilisi|european union|europe|emea|apac|latam|czechia|czech republic|romania|bulgaria|hungary|ukraine|russia|belarus|turkey|turkiye|greece|italy|austria|belgium|luxembourg|estonia|latvia|lithuania|croatia|costa rica|panama|uruguay|ecuador|cambodia|laos|nepal|sri lanka|bangladesh|lebanon|jordan)\b',re.I)


def _location_signal(location, country=''):
    text=''.join(c for c in unicodedata.normalize('NFKD',location or '') if not unicodedata.combining(c))
    country=(country or '').strip().upper()
    if country and country not in {'US','USA','UNITED STATES','UNITED STATES OF AMERICA'}:
        return 'outside_us'
    if re.search(r'\b(?:non[ -]?(?:us|usa)|(?:excluding|except|outside(?:\s+of)?|not(?:\s+available|\s+eligible)?(?:\s+in|\s+to)?)\s+(?:the\s+)?(?:united states|usa|u\.s\.?|us))\b',text,re.I):
        return 'outside_us'
    if US_TOKEN.search(text): return 'us'
    if not country and re.search(r'\bOntario\s*,\s*CA\b',text,re.I):return 'unknown'
    # A real US state code disambiguates Paris, TX / Berlin, NJ. Canada/CA
    # and Georgia alone are ambiguous; foreign locations must not prove US eligibility.
    state_code=US_STATE_CODE.search(text)
    foreign=FOREIGN.search(text)
    if state_code and not (foreign and state_code.group().strip(' ,')=='CA'):
        return 'us'
    if foreign:
        alternatives=re.split(r'\s*[;·|/]\s*|\s+(?:or|and|&)\s+',text,flags=re.I)
        if len(alternatives)>1 and any(_location_signal(part)=='us' for part in alternatives):
            return 'us'
        return 'outside_us'
    if country or US_PLACE.search(text): return 'us'
    return 'unknown'


def location_status(location, country='', title=''):
    result=_location_signal(location,country)
    if result=='us' and title:
        # Some aggregate feeds mislabel a foreign vacancy as US. A location
        # suffix in the title can contradict that metadata; quarantine it.
        suffix=re.search(r'\b(?:remote|based in|located in)\s*[:\-]?\s*(.+)$',title,re.I)
        parenthetical=re.findall(r'\(([^)]+)\)',title)
        scopes=([suffix.group(1)] if suffix else [])+parenthetical
        if any(_location_signal(part)=='outside_us' and not US_TOKEN.search(part) for part in scopes):
            return 'unknown'
    return result


MARKETS = {'us':'United States', 'ca':'Canada', 'uk':'United Kingdom'}
SUPPORTED_LOCATIONS = ('us','ca','uk','us_ca','us_uk','ca_uk','us_ca_uk')
COUNTRY_CODES = {'US':'us','USA':'us','UNITED STATES':'us','UNITED STATES OF AMERICA':'us',
                 'CA':'ca','CAN':'ca','CANADA':'ca','GB':'uk','GBR':'uk','UK':'uk','UNITED KINGDOM':'uk'}
CANADA = re.compile(r'\b(?:canada|toronto|vancouver|montreal|ottawa|calgary|edmonton|waterloo|ontario|british columbia|quebec|alberta|nova scotia|manitoba|saskatchewan|new brunswick|newfoundland|prince edward island)\b|,\s*(?-i:ON|BC|QC|AB|NS|MB|SK|NB|NL|PE)(?=\s*(?:[,;)·|]|$))',re.I)
UK = re.compile(r'\b(?:united kingdom|u\.k\.?|uk|england|scotland|wales|northern ireland|edinburgh)\b',re.I)


def market_location_status(location, country='', title=''):
    """Positive market evidence, with contradictory or ambiguous metadata held out.

    Multi-location records keep one identity and can belong to several markets.
    Bare London and Ontario, CA deliberately do not establish a new market.
    """
    text=''.join(c for c in unicodedata.normalize('NFKD',location or '') if not unicodedata.combining(c))
    code=(country or '').strip().upper()
    structured=COUNTRY_CODES.get(code)
    if code and not structured:return 'outside_us'
    parts=re.split(r'\s*[;·|/]\s*|\s+(?:or|and|&)\s+',text,flags=re.I)
    found=set()
    for part in parts:
        # Negated country names cannot supply positive evidence.
        if re.search(r'\b(?:non[- ]|excluding\s+|except\s+|outside\s+(?:of\s+)?|not\s+(?:(?:available|eligible)\s+)?(?:in\s+|to\s+)?)',part,re.I):
            continue
        is_us=_location_signal(part)=='us'
        if is_us:found.add('us')
        if re.search(r'\bOntario\s*,\s*CA\b',part,re.I) and structured!='ca':continue
        if US_STATE_CODE.search(part) and is_us:continue
        remainder=US_PLACE.sub('',US_TOKEN.sub('',UK.sub('',CANADA.sub('',part))))
        if UK.search(part) or CANADA.search(part) or structured in {'ca','uk'}:remainder=re.sub(r'\bLondon\b','',remainder,flags=re.I)
        if not is_us and FOREIGN.search(remainder):continue
        if CANADA.search(part):found.add('ca')
        if UK.search(part):found.add('uk')
        elif re.fullmatch(r'\s*(?:Remote[ ,:-]*)?(?:CA|CAN)\s*',part):found.add('ca')
        elif re.fullmatch(r'\s*(?:Remote[ ,:-]*)?(?:GB|GBR)\s*',part):found.add('uk')
    if structured:
        if len(parts)==1 and found and structured not in found:return 'unknown'
        if not found and FOREIGN.search(text) and not (structured=='uk' and re.search(r'\bLondon\b',text,re.I)):
            return 'unknown'
        found.add(structured)
    if title and found:
        suffix=re.search(r'\b(?:remote|based in|located in)\s*[:\-]?\s*(.+)$',title,re.I)
        scopes=([suffix.group(1)] if suffix else [])+re.findall(r'\(([^)]+)\)',title)
        for scope in scopes:
            other=market_location_status(scope)
            if (other in SUPPORTED_LOCATIONS and not found.intersection(other.split('_'))) or (other=='outside_us'):
                return 'unknown'
    if found:return '_'.join(k for k in MARKETS if k in found)
    return 'outside_us' if FOREIGN.search(text) else 'unknown'


def market_countries(status):
    return status.split('_') if status in SUPPORTED_LOCATIONS else []


def market_location_label(location, country='', work_model='unknown', status=None):
    status=status or market_location_status(location,country)
    if status=='us':return us_location_label(location,country,work_model)
    # Retain raw multi-location evidence in details, but keep unrelated markets off the row.
    parts=re.split(r'\s*[;·|/]\s*',location or '')
    selected=[p for p in parts if market_location_status(p) in SUPPORTED_LOCATIONS]
    if selected:return ' · '.join(selected[:2])+(f' +{len(selected)-2} locations' if len(selected)>2 else '')
    return ' / '.join(MARKETS[k] for k in market_countries(status)) or location


ROLE_FAMILIES={
    'data':r'\b(?:data engineer|analytics engineer|data platform)\b',
    'ai':r'\b(?:machine learning|ml engineer|ai engineer|research engineer|artificial intelligence)\b',
    'quality':r'\b(?:qa|sdet|test|quality|automation)\b',
    'infrastructure':r'\b(?:devops|sre|site reliability|cloud|infrastructure|platform|production|release)\b',
    'security':r'\b(?:security|cybersecurity|cyber)\b',
    'embedded':r'\b(?:embedded|firmware)\b',
    'software':r'.*',
}


def job_attributes(title, location='', work_model=''):
    model=(work_model or '').lower().replace('-','').replace('_','').replace(' ','')
    if model not in {'remote','hybrid','onsite'}:
        match=re.search(r'\bhybrid\b',location or '',re.I) or re.search(r'\b(remote|on[ -]?site)\b',location or '',re.I)
        model=match.group(0).lower().replace('-','').replace(' ','') if match else 'unknown'
    return {'role_family':next(k for k,p in ROLE_FAMILIES.items() if re.search(p,title,re.I)),
            'work_model':model,'early_career':int(bool(EARLY.search(title)) and not SENIOR.search(title)),
            'cohort_2027':int(bool(re.search(r'\b2027\b',title) and EARLY.search(title)) and not SENIOR.search(title))}


def us_location_label(location, country='', work_model='unknown'):
    """Keep the US options on the compact row; original locations stay in details."""
    parts=re.split(r'\s*[;·]\s*',location or '')
    us_parts=[p for p in parts if _location_signal(p)=='us' and not FOREIGN.search(p)]
    if not us_parts:
        if FOREIGN.search(location or ''):return 'US option listed'
        return location or ('US remote' if work_model=='remote' else 'United States')
    return ' · '.join(us_parts[:2])+(f' +{len(us_parts)-2} US locations' if len(us_parts)>2 else '')


def classify(title, description, location='', country='', start_date='2027-06-01'):
    text = plain(description)
    loc = market_location_status(location, country, title)
    international = any(k in market_countries(loc) for k in ('ca','uk'))
    evidence = []
    for sentence in re.split(r'(?<=[!?;])\s+|\n+|(?<=[a-z0-9])\.\s+(?=[A-Z])', text):
        negative = any(re.search(p, sentence, re.I) for p in NEGATIVE)
        if international:
            negative = negative or any(re.search(p,sentence,re.I) for p in [
                r'\b(?:must|need to)\s+be\s+(?:a\s+)?(?:canadian|british|uk)\s+citizen\b',
                r'\b(?:canadian|british|uk)\s+(?:citizens?\s+only|citizenship\s+(?:is\s+)?required)\b',
                r'\b(?:must|need to|required to)\b[^.!?]{0,60}\b(?:already|currently)\b[^.!?]{0,50}\b(?:authori[sz]ed|right to work|work (?:permit|authori[sz]ation))\b',
                r'\b(?:must|need to|required to)\b[^.!?]{0,60}\b(?:unrestricted|permanent|existing)\s+(?:right to work|work authori[sz]ation)\b',
                r'\b(?:must|need to|required to)\b[^.!?]{0,60}\b(?:right to work|authori[sz]ed to work|eligible to work)\b[^.!?]{0,50}\b(?:uk|united kingdom|canada)\b(?![^.!?]{0,60}\bsponsor)',
                r'\b(?:cannot|unable to|do not|does not|will not)\s+(?:offer|provide|support)\s+(?:a\s+)?(?:skilled worker|work (?:visa|permit)|visa)\b',
                r'\bno\s+(?:skilled worker|work permit)\s+sponsorship\b',
                r'\b(?:canadian|british|uk)\s+(?:citizens?|permanent residents?)\s+(?:or\s+(?:permanent residents?|citizens?)\s+)?only\b',
            ])
        positive = not negative and any(re.search(p, sentence, re.I) for p in POSITIVE)
        if negative or positive:
            evidence.append({'kind': 'restriction' if negative else 'support', 'quote': sentence[:1200], 'origin': 'posting'})
    blocked = any(e['kind'] == 'restriction' for e in evidence)
    supports = any(e['kind'] == 'support' for e in evidence)
    sponsorship = 'conflict' if blocked and supports else 'restricted' if blocked else 'supported' if supports else 'unknown'
    reasons = []
    scope = True
    if not ROLE.search(title):
        scope = False
        reasons.append('Outside software and related engineering titles')
    if re.search(r'\bfront[ -]?end\b',title,re.I) and not re.search(r'\b(?:software|engineer|developer|programmer|swe|sde)\b',title,re.I):
        scope=False
        reasons.append('Front-end title does not identify a software engineering occupation')
    if re.search(r'\b(land developer|property developer|mechanical|fluids|civil engineer|manufacturing.*quality|quality.*manufacturing)\b', title,re.I) and not re.search(r'software|firmware|embedded|automation',title,re.I):
        scope = False
        reasons.append('Explicit engineering specialty outside software-related roles')
    if SENIOR.search(title):
        scope = False
        reasons.append('Explicit seniority outside entry level')
    if re.search(r'\b(interns?|internships?|co-op)\b', title, re.I):
        scope = False
        reasons.append('Internship, not a full-time graduate role')
    # Experience in the actual requirement, never unrelated numbers elsewhere in the posting.
    years = []
    for m in re.finditer(r'\b(\d{1,2})\s*(?:(?:[-–]|to)\s*\d{1,2}|\+)?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:[^.;\n]{0,90}?\b)?(?:experience|building|developing|designing|shipping|working)\b', text, re.I):
        context = text[max(0, m.start()-70):m.end()+60]
        if not re.search(r'preferred|nice.to.have|bonus|desirable', context, re.I):
            years.append(int(m.group(1)))
    min_years = min(years) if years else None
    if min_years is not None and min_years > 3:
        scope = False
        reasons.append(f'Requires at least {min_years} years of experience')
    if min_years is None and not EARLY.search(title):
        reasons.append('Experience requirement needs verification')
    if loc not in SUPPORTED_LOCATIONS:
        scope = False
        reasons.append('Location is outside US, Canada and UK' if loc=='outside_us' else 'Work location in US, Canada or UK is unconfirmed')
    if re.search(r'\b202[0-6]\b', title) and not re.search(r'\b202[7-9]\b', title) and EARLY.search(title):
        scope = False
        reasons.append('Explicit graduate cohort predates 2027')
    # Do not invent a start date from a publication date or graduation-year guess.
    for m in re.finditer(r'\b(?:start date|starting|must start|start by)\s*:?\s*(20\d\d-\d\d-\d\d)', text, re.I):
        if m.group(1) < start_date:
            scope = False
            reasons.append('Explicit start date is before availability')
    return {'in_scope': int(scope), 'scope_reasons': reasons, 'min_years': min_years,
            'location_status': loc, 'sponsorship': sponsorship, 'evidence': evidence}
