"""Documented public feeds only. No login scraping or anti-bot workarounds."""
import asyncio
import html
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlencode

import httpx
from bs4 import BeautifulSoup

from .domain import canonical_url, plain, ROLE, SENIOR, COUNTRY_CODES, MARKETS
from .catalog import DIRECTORIES, parse_directory

ATS = {'greenhouse', 'ashby', 'lever', 'recruitee', 'smartrecruiters'}
AGGREGATORS = {'remotive', 'jobicy', 'himalayas'}
PROVIDERS = ATS | AGGREGATORS | {'github', 'directory'}


def validate_source(provider, slug):
    if provider not in PROVIDERS:
        raise ValueError('Unknown source platform')
    if provider == 'directory':
        if slug not in DIRECTORIES: raise ValueError('Unknown employer directory')
        return
    if provider in AGGREGATORS:
        if slug != 'us-engineering': raise ValueError('Choose the US engineering feed')
        return
    pattern = r'[\w.-]+/[\w.-]+(?:/[\w.-]+)*' if provider == 'github' else r'[\w.-]+'
    if not re.fullmatch(pattern, slug, flags=re.ASCII) or '..' in slug:
        raise ValueError('Invalid employer board identifier or owner/repository')


def board_from_url(url):
    try:
        p = urlsplit(html.unescape(url))
        parts = [unquote(x) for x in p.path.split('/') if x]
        host = (p.hostname or '').lower()
        provider = ('greenhouse' if host in {'boards.greenhouse.io','job-boards.greenhouse.io'} else
                    'ashby' if host == 'jobs.ashbyhq.com' else
                    'lever' if host == 'jobs.lever.co' else None)
        if host in {'jobs.smartrecruiters.com','www.smartrecruiters.com'}:provider='smartrecruiters'
        if host.endswith('.recruitee.com') and host.count('.') == 2:
            token = host.split('.')[0]
            validate_source('recruitee', token)
            return 'recruitee', token
        if provider and parts:
            if provider=='greenhouse' and parts[0]=='embed':
                from urllib.parse import parse_qs
                token=parse_qs(p.query).get('for',[''])[0]
                validate_source(provider,token)
                return provider,token
            validate_source(provider,parts[0])
            return provider,parts[0]
    except (ValueError, IndexError):
        pass
    return None


class Fetcher:
    def __init__(self, client):
        self.client = client
        self.gates = {}
        self.next_request = {}

    async def pace(self, url):
        host = urlsplit(url).hostname
        # Public Lever API requests must be at least one second apart, including
        # pagination and retries. Other shared hosts get at most four per second.
        interval = 1.05 if host == 'api.lever.co' else .27 if host in {
            'boards-api.greenhouse.io', 'api.ashbyhq.com', 'raw.githubusercontent.com', 'api.smartrecruiters.com'
        } else 1.1 if host in {'jobicy.com','himalayas.app','remotive.com'} else .1
        lock = self.gates.setdefault(host, asyncio.Lock())
        async with lock:
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0, self.next_request.get(host, 0) - loop.time()))
            self.next_request[host] = loop.time() + interval

    async def get(self, url):
        for attempt in range(3):
            try:
                await self.pace(url)
                response = await self.client.get(url, follow_redirects=False)
                if response.status_code in (429,500,502,503,504) and attempt < 2:
                    delay = response.headers.get('retry-after', str(2**attempt))
                    await asyncio.sleep(min(10, float(delay)) if delay.replace('.','',1).isdigit() else 2**attempt)
                    continue
                response.raise_for_status()
                if len(response.content) > 50_000_000:
                    raise ValueError('Source response exceeds the collection size limit')
                return response
            except (httpx.TimeoutException,httpx.NetworkError):
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)
        raise RuntimeError('Source could not be fetched')

    async def collect(self, source):
        provider, slug = source['provider'],source['slug']
        validate_source(provider,slug)
        if provider == 'directory':
            response = await self.get(DIRECTORIES[slug])
            return [], await asyncio.to_thread(parse_directory, response.text, slug)
        if provider == 'github':
            owner, repo, *path = slug.split('/')
            filename = '/'.join(path) or 'README.md'
            response = await self.get(f'https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{filename}')
            parser = parse_community_json if filename.endswith('.json') else parse_github
            return await asyncio.to_thread(parser,response.text,source['name']), []
        if provider in AGGREGATORS:
            return await self.aggregate(provider), []
        if provider == 'smartrecruiters':
            rows=[];seen=set()
            # A single complete snapshot covers all three requested countries.
            # Any failed country rejects the snapshot, preserving prior availability.
            for country in ('us','ca','gb'):
                count=0
                for page in range(100):
                    data=(await self.get(f'https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={page*100}&country={country}')).json()
                    if not isinstance(data,dict) or not isinstance(data.get('content'),list) or not isinstance(data.get('totalFound'),int):
                        raise ValueError('SmartRecruiters schema changed; snapshot rejected')
                    batch=data['content'];count+=len(batch)
                    for row in batch:
                        if row['id'] not in seen:seen.add(row['id']);rows.append(row)
                    if count>=data['totalFound']:break
                    if not batch:raise ValueError('Incomplete SmartRecruiters snapshot')
                else:raise ValueError('SmartRecruiters pagination limit reached')
            jobs=[]
            for row in rows:
                if ROLE.search(row['name']) and not SENIOR.search(row['name']):
                    # Construct the endpoint; never follow arbitrary URLs from a feed.
                    identifier=str(row['id'])
                    if not identifier.isdigit():raise ValueError('Invalid SmartRecruiters posting ID')
                    row=(await self.get(f'https://api.smartrecruiters.com/v1/companies/{slug}/postings/{identifier}')).json()
                    if not isinstance(row.get('jobAd',{}).get('sections'),dict):
                        raise ValueError('SmartRecruiters description missing; snapshot rejected')
                jobs.append(smartrecruiters(row,source))
            return jobs,[]
        if provider == 'greenhouse':
            data = (await self.get(f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true')).json()
            if not isinstance(data,dict) or not isinstance(data.get('jobs'),list):
                raise ValueError('Greenhouse response has changed; snapshot rejected')
            rows = data['jobs']
            if data.get('meta',{}).get('total',len(rows)) != len(rows):
                raise ValueError('Incomplete Greenhouse snapshot')
            return await asyncio.to_thread(lambda:[greenhouse(j,source) for j in rows if j.get('internal_job_id') is not None]), []
        if provider == 'ashby':
            data = (await self.get(f'https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true')).json()
            if not isinstance(data,dict) or not isinstance(data.get('jobs'),list):
                raise ValueError('Ashby response has changed; snapshot rejected')
            return await asyncio.to_thread(lambda:[ashby(j,source) for j in data['jobs'] if j.get('isListed') is True]), []
        if provider == 'recruitee':
            data = (await self.get(f'https://{slug}.recruitee.com/api/offers/')).json()
            if not isinstance(data,dict) or not isinstance(data.get('offers'),list):
                raise ValueError('Recruitee response has changed; snapshot rejected')
            return await asyncio.to_thread(lambda:[recruitee(j,source) for j in data['offers']]), []
        rows = []
        for page in range(100):
            batch = (await self.get(f'https://api.lever.co/v0/postings/{slug}?mode=json&skip={page*100}&limit=100')).json()
            if not isinstance(batch,list):
                raise ValueError('Lever response has changed; snapshot rejected')
            rows.extend(batch)
            if len(batch)<100:
                return await asyncio.to_thread(lambda:[lever(j,source) for j in rows]), []
        raise ValueError('Lever pagination limit reached; partial snapshot rejected')

    async def aggregate(self, provider, country=None):
        if provider=='himalayas' and country is None:
            jobs={}
            for market in ('US','CA','GB'):
                for job in await self.aggregate(provider,market):jobs[job['external_id']]=job
            return list(jobs.values())
        jobs, seen, cursors = [], set(), set()
        cursor = None
        for page in range(1,10001):
            if provider == 'remotive':
                url = 'https://remotive.com/api/remote-jobs'  # all categories; related roles may be outside software-dev
            elif provider == 'jobicy':
                params = {'count':200}
                if cursor: params['cursor'] = cursor
                url = 'https://jobicy.com/api/v2/remote-jobs?' + urlencode(params)
            else:
                url = 'https://himalayas.app/jobs/api/search?' + urlencode({
                    'country':country,'seniority':'Entry-level,Mid-level','sort':'recent','page':page})
            data = (await self.get(url)).json()
            rows = data.get('jobs')
            if not isinstance(rows,list) or data.get('success') is False:
                raise ValueError(f'{provider} schema changed; snapshot rejected')
            for row in rows:
                item = aggregate_job(row,provider)
                if not ROLE.search(item['title']): continue
                if item['external_id'] not in seen:
                    seen.add(item['external_id']); jobs.append(item)
            if provider == 'remotive' or provider == 'himalayas' and not rows:
                return jobs
            if provider == 'jobicy':
                cursor = data.get('nextCursor')
                if not cursor:
                    if data.get('hasMore'): raise ValueError('Jobicy cursor missing; partial feed rejected')
                    return jobs
                if not isinstance(cursor,str) or cursor in cursors:
                    raise ValueError('Jobicy pagination repeated; partial feed rejected')
                cursors.add(cursor)
        raise ValueError(f'{provider} pagination limit reached; partial feed rejected')


def greenhouse(j, source):
    return dict(external_id=str(j['id']), title=j['title'], company=source['name'],
                location=(j.get('location') or {}).get('name',''), country='',
                description=plain(html.unescape(j.get('content',''))), apply_url=j['absolute_url'],
                # updated_at is NOT the original posting date.
                posted_at=j.get('first_published'),posted_kind='employer_publication')


def ashby(j,source):
    secondary = j.get('secondaryLocations') or []
    locations = [j.get('location','')]+[s.get('location','') for s in secondary]
    countries = [((j.get('address') or {}).get('postalAddress') or {}).get('addressCountry','')]
    countries += [((s.get('address') or {}).get('postalAddress') or s.get('address') or {}).get('addressCountry','') for s in secondary]
    country = next((c for market in ('us','ca','uk') for c in countries if COUNTRY_CODES.get(c.upper())==market),next((c for c in countries if c),''))
    return dict(external_id=j.get('id') or j['jobUrl'], title=j['title'], company=source['name'],
                location=' · '.join(f"{loc}, {MARKETS.get(COUNTRY_CODES.get(code.upper()),'[country='+code+']')}" if code else loc for loc,code in zip(locations,countries) if loc or code),country=country,
                description=j.get('descriptionPlain') or plain(j.get('descriptionHtml','')),
                apply_url=j.get('applyUrl') or j['jobUrl'], posted_at=j.get('publishedAt'),posted_kind='employer_publication',
                work_model=j.get('workplaceType',''),employment_type=j.get('employmentType',''))


def lever(j,source):
    categories = j.get('categories') or {}
    description = plain(j.get('description','')) + '\n' + '\n'.join(
        plain(x.get('text',''))+'\n'+plain(x.get('content','')) for x in (j.get('lists') or []))
    description += '\n' + plain(j.get('additional',''))
    return dict(external_id=j['id'], title=j['text'], company=source['name'], location=categories.get('location') or '',
                country=j.get('country') or '', description=description, apply_url=j.get('applyUrl') or j['hostedUrl'],
                posted_at=None,work_model=j.get('workplaceType',''),employer_level=categories.get('level',''))


def recruitee(j,source):
    locations = j.get('locations') or []
    countries = [x.get('country_code') or x.get('country','') for x in locations]
    country = next((c for market in ('us','ca','uk') for c in countries if COUNTRY_CODES.get(c.upper())==market),j.get('country_code') or j.get('country',''))
    def location_label(item):
        code=item.get('country_code','')
        country=item.get('country') or MARKETS.get(COUNTRY_CODES.get(code.upper()),'[country='+code+']' if code else '')
        return ', '.join(str(x) for x in (item.get('city'),item.get('state'),country) if x)
    location = ' · '.join(location_label(x) for x in locations) or j.get('location','')
    return dict(external_id=str(j['id']),title=j['title'],company=j.get('company_name') or source['name'],
                location=location,country=country,description=plain(j.get('description',''))+'\n'+plain(j.get('requirements','')),
                apply_url=j.get('careers_apply_url') or j['careers_url'],posted_at=j.get('published_at'),posted_kind='employer_publication',
                work_model='hybrid' if j.get('hybrid') else 'remote' if j.get('remote') else 'onsite' if j.get('on_site') else '')


def smartrecruiters(j,source):
    location=j.get('location') or {};sections=(j.get('jobAd') or {}).get('sections') or {}
    identifier=str(j['id'])
    if not identifier.isdigit():raise ValueError('Invalid SmartRecruiters posting ID')
    return dict(external_id=identifier,title=j['name'],company=(j.get('company') or {}).get('name') or source['name'],
                location=location.get('fullLocation') or ', '.join(location[k] for k in ('city','region','country') if location.get(k)),
                country=location.get('country','').upper(),description='\n'.join(plain(x.get('text','')) for x in sections.values()),
                apply_url=j.get('applyUrl') or f"https://jobs.smartrecruiters.com/{source['slug']}/{identifier}",
                posted_at=j.get('releasedDate'),posted_kind='employer_publication',
                employer_level=(j.get('experienceLevel') or {}).get('label',''),
                employment_type='FullTime' if (j.get('typeOfEmployment') or {}).get('label')=='Full-time' else (j.get('typeOfEmployment') or {}).get('label',''),
                work_model='hybrid' if location.get('hybrid') else 'remote' if location.get('remote') else 'onsite')


def aggregate_job(j,provider):
    if provider == 'remotive':
        return dict(external_id=str(j['id']),title=j['title'],company=j['company_name'],
                    location=j.get('candidate_required_location',''),country='',description=plain(j.get('description','')),
                    apply_url=j['url'],posted_at=j.get('publication_date'),work_model='remote')
    if provider == 'jobicy':
        return dict(external_id=str(j['id']),title=j['jobTitle'],company=j['companyName'],
                    location=j.get('jobGeo',''),country='',description=plain(j.get('jobDescription','')),
                    apply_url=j['url'],posted_at=j.get('pubDate'),work_model='remote')
    countries=j.get('locationRestrictions') or []
    # Preserve the actual country restrictions. Empty means worldwide, not a US-only role.
    country=next((c for market in ('us','ca','uk') for c in countries if COUNTRY_CODES.get(c.upper())==market),countries[0] if countries else '')
    country={'us':'US','ca':'CA','uk':'GB'}.get(COUNTRY_CODES.get(country.upper()),country)
    from datetime import datetime,timezone
    posted=j.get('pubDate')
    if isinstance(posted,(int,float)): posted=datetime.fromtimestamp(posted,timezone.utc).isoformat()
    return dict(external_id=j['guid'],title=j['title'],company=j['companyName'],
                location=' · '.join(MARKETS.get(COUNTRY_CODES.get(c.upper()),c) for c in countries) or 'Worldwide remote',country=country,description=plain(j.get('description','')),
                # Keep the provider's canonical page for attribution and application access.
                apply_url=j['guid'],posted_at=posted,work_model='remote')


def parse_community_json(text, source_name):
    data=json.loads(text)
    if not isinstance(data,dict) or not isinstance(data.get('jobs'),list):
        raise ValueError('Community JSON feed schema changed')
    jobs=[]
    for row in data['jobs']:
        if data.get('api_version') == 1:  # Ricsign: only the full-time SWE board
            if row.get('track') != 'new-grad-swe' or row.get('active') is not True: continue
            title,company,url=row['title'],row['company_name'],row['apply_url']
            location=' · '.join(row.get('locations') or [])
            posted=row.get('posted_at')
        elif 'updatedAt' in data:  # ApplyGuy: use employer listing, never one-click-apply redirect
            title,company,url=row['title'],row['company'],row['listingUrl']
            location,posted=row.get('location',''),row.get('posted')
        else:
            raise ValueError('Unrecognized community JSON feed')
        jobs.append(dict(external_id=canonical_url(url),title=title,company=company,location=location,
                         country='',description='',apply_url=url,posted_at=posted))
    if not jobs: raise ValueError('No recognizable open jobs in community JSON feed')
    return jobs


def parse_github(markdown, source_name):
    """Handle HTML and Markdown tables. A broken layout is a failure, never zero jobs."""
    soup = BeautifulSoup(markdown,'html.parser')
    rows = []
    for tr in soup.select('tr'):
        cells = tr.find_all('td',recursive=False)
        if len(cells)<3:
            continue
        links = [a['href'] for c in cells[2:] for a in c.select('a[href]')]
        link = next((u for u in links if '/apply' in u or board_from_url(u)),next((u for u in links if u.startswith('http')),None))
        rows.append(([c.get_text(' ',strip=True) for c in cells],link))
    for line in markdown.splitlines():
        if not line.strip().startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells)<4 or re.fullmatch(r'[: -]+',cells[0]):
            continue
        # Jobright's public list links the role itself; its website is never fetched.
        link_cells=cells[1:2] if 'jobright.ai/jobs/' in cells[1] else cells[3:]
        candidates = re.findall(r'\]\((https?://[^\s)]+)\)|href=["\x27](https?://[^"\x27]+)', '|'.join(link_cells))
        links = [a or b for a,b in candidates]
        link = next((u for u in links if '/apply' in u or board_from_url(u)),next(iter(links),None))
        rows.append(([plain(re.sub(r'\[([^\]]+)\]\([^)]+\)',r'\1',c)) for c in cells],link))
    jobs, company = [], ''
    seen = set()
    for cells,link in rows:
        if cells[0] not in {'↳','↳️',''}:
            company = cells[0].replace('**','')
        if not link or '🔒' in ''.join(cells) or not company:
            continue
        try:
            url = canonical_url(html.unescape(link))
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        annotations = []
        if '🛂' in ''.join(cells): annotations.append('Sponsorship eligibility flagged by community list (🛂); employer confirmation required.')
        if '🇺🇸' in ''.join(cells): annotations.append('Community list reports US citizenship restriction; employer confirmation required.')
        # Community annotations are not employer evidence and cannot exclude a job.
        jobs.append(dict(external_id=url,apply_url=url,title=cells[1].replace('**',''),company=company,
                         location=cells[2],country='',description='\n'.join(annotations),posted_at=None,
                         work_model=cells[3] if 'jobright.ai/' in url else ''))
    if not jobs:
        raise ValueError('No recognizable open jobs in GitHub list; layout needs review')
    return jobs


def load_seeds(db):
    path = Path(__file__).resolve().parent.parent / 'sources.json'
    for source in json.loads(path.read_text()):
        validate_source(source['provider'],source['slug'])
        db.add_source(**source)
