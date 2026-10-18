from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json

import httpx
import asyncio
import pytest
from fastapi.testclient import TestClient

from tracker.app import create_app
from tracker.config import Config
from tracker.db import Database
from tracker.domain import classify
from tracker.editions import cutoff_for,next_cutoff,timestamp,eligibility
from tracker.sources import Fetcher,board_from_url,parse_github,smartrecruiters

UTC=timezone.utc
END=datetime(2026,10,17,22,30,tzinfo=UTC)


def job(i=1,**kw):
    return dict(external_id=str(i),title=kw.pop('title',f'Software Engineer I, Team {i}'),company='Acme',
                location='New York, NY',country='US',description=kw.pop('description','0-2 years of experience. Visa sponsorship is available.'),
                apply_url=kw.pop('apply_url',f'https://jobs.ashbyhq.com/acme/{i}'),
                posted_at=kw.pop('posted_at','2026-10-17T15:00:00+00:00'),posted_kind=kw.pop('posted_kind','employer_publication'),**kw)


@pytest.fixture
def setup(tmp_path):
    db=Database(tmp_path/'tracker.sqlite3');db.add_source('ashby','acme','Acme')
    return db,db.sources()[0]


def ingest(db,s,jobs):
    db.ingest(s,jobs,None,'2027-06-01')
    with db.connect() as c:
        c.execute('UPDATE jobs SET first_seen=?,verified_at=?',('2026-10-17T17:00:00+00:00','2026-10-17T17:00:00+00:00'))


@pytest.mark.parametrize('title',[
    'Principal Software Engineer I - Serverless - Platform Control Plane',
    'Senior Software Engineer I','Senior Endpoint Security Engineer I (6701)',
    'Sr. AI Engineer I (6811)','Associate Director, Software Engineering',
    'Staff Software Engineer - Early Career Mentorship',
])
def test_screenshot_seniority_cannot_be_overridden(title):
    assert classify(title,'0-2 years of experience.','New York, NY')['in_scope']==0
    assert not eligibility({**job(title=title),'verified_at':'2026-10-17T17:00:00Z'},strict_cohort=False)['eligible']


@pytest.mark.parametrize('title,description,expected',[
    ('Associate, Data Engineer','3+ years of experience.',False),
    ('AWS Data Engineer (Associate)','1+ years of experience.',False),
    ('Software Engineer I','8+ years of experience.',False),
    ('Software Engineer I','1 or more years of experience.',False),
    ('Software Engineer I',"Five (5) years’ experience required.",False),
    ('Software Engineer I','Up to 3 years of experience.',True),
    ('Software Engineer I','Two years of professional experience required.',False),
    ('Software Engineer I','0-3 years of experience.',True),
    ('Software Engineer I','No prior experience required.',True),
    ('Software Engineer','0-2 years of professional experience.',True),
    ('Software Engineer','Design great software.',False),
    ('Software Engineer I','5 years of experience preferred.',True),
    ('Software Engineer I','0 years with a PhD or 3 years with a Bachelors.',False),
    ('Software Engineer PhD New Grad 2027','0 years of experience.',False),
    ('Software Engineer I','Start date: January 2027.',False),
    ('Software Engineer I','Starting June 2027.',True),
    ('Software Engineer I','Immediate start required.',False),
    ('Software Engineer I','Start date: Jan 2027.',False),
    ('Software Engineer I','Starting February 31, 2027.',False),
    ('Software Engineer I','A Masters degree is required.',False),
    ('Software Engineer I','Must have a PhD in Computer Science.',False),
    ('Software Engineer I','Bachelors or Masters degree required.',True),
    ('Software Engineer I','Graduating in 2028 required.',False),
    ('Software Engineer New Grad 2028','0 years of experience.',False),
])
def test_daily_eligibility(title,description,expected):
    j={**job(title=title,description=description),'verified_at':'2026-10-17T17:00:00Z'}
    assert eligibility(j,strict_cohort=False)['eligible']==expected


def test_cohort_policy_is_explicit(setup):
    j={**job(),'verified_at':'2026-10-17T17:00:00Z'}
    assert eligibility(j,strict_cohort=False)['eligible']
    assert not eligibility(j,strict_cohort=True)['eligible']
    j['description']+=' Graduating between December 2026 and August 2027.'
    assert eligibility(j,strict_cohort=True)['eligible']


@pytest.mark.parametrize('value',['2026-10-17',None,'bad','2026-10-17T13:00:00','2026-13-17T13:00:00Z'])
def test_unknown_timestamps_never_become_24h_evidence(value):
    assert timestamp(value) is None


def test_fixed_100_cap_without_topups(setup):
    db,s=setup;ingest(db,s,[job(i) for i in range(120)])
    edition_id=db.publish_edition(END,'2027-06-01',False)
    result=db.edition();assert result['edition']['total']==100 and len(result['jobs'])==100
    ids=[j['id'] for j in result['jobs']]
    db.decide(ids[0],{'status':'applied'});db.decide(ids[1],{'status':'dismissed'})
    result=db.edition();assert result['total']==98 and result['handled']==2 and result['edition']['total']==100
    assert db.publish_edition(END,'2027-06-01',True)==edition_id
    assert {j['id'] for j in result['jobs']}==set(ids[2:])
    with db.connect() as c:c.execute("UPDATE jobs SET description='Updated after cutoff' WHERE id=?",(ids[2],))
    # The retained snapshot is immutable even when an employer edits its description.
    assert next(j for j in db.edition()['jobs'] if j['id']==ids[2])['description'].startswith('0-2')


def test_smaller_edition_is_not_padded(setup):
    db,s=setup;ingest(db,s,[job(i) for i in range(15)])
    db.publish_edition(END,'2027-06-01',False)
    assert db.edition()['total']==15


def test_posting_window_and_authority_not_discovery_time(setup):
    db,s=setup
    rows=[job(1),job(2,posted_at='2026-10-16T22:30:00Z'),job(3,posted_at='2026-10-17T22:30:00Z'),
          job(4,posted_at='2026-10-17T22:30:01Z'),job(5,posted_at=None),job(6,posted_at='2026-10-17'),
          job(7,posted_kind='unknown'),job(8,posted_at='2026-10-01T00:00:00Z')]
    ingest(db,s,rows)
    db.publish_edition(END,'2027-06-01',False)
    assert {j['title'] for j in db.edition()['jobs']}=={rows[0]['title'],rows[2]['title']}


def test_no_unverified_conflicting_or_already_applied_jobs(setup):
    db,s=setup;ingest(db,s,[job(i) for i in range(1,6)])
    with db.connect() as c:
        c.execute("UPDATE jobs SET verified_at=NULL WHERE canonical_url LIKE '%/1'")
        c.execute("UPDATE jobs SET sponsorship='conflict' WHERE canonical_url LIKE '%/2'")
        c.execute("UPDATE jobs SET status='applied' WHERE canonical_url LIKE '%/3'")
        c.execute("UPDATE jobs SET first_seen='2026-10-17T23:00:00Z' WHERE canonical_url LIKE '%/4'")
    db.publish_edition(END,'2027-06-01',False)
    assert db.edition()['total']==1 and db.edition()['jobs'][0]['title'].endswith('5')


def test_repeat_urls_and_reposts_do_not_create_another_edition_entry(setup):
    db,s=setup;ingest(db,s,[job()]);first=db.publish_edition(END,'2027-06-01',False)
    original=db.edition()['jobs'][0]
    db.decide(original['id'],{'status':'applied'})
    ingest(db,s,[job(2,title=job()['title'],posted_at='2026-10-18T15:00:00Z')])
    with db.connect() as c:c.execute("UPDATE jobs SET first_seen='2026-10-18T16:00:00Z',verified_at='2026-10-18T16:00:00Z'")
    second=db.publish_edition(END+timedelta(days=1),'2027-06-01',False)
    assert second!=first and db.edition()['total']==0
    assert db.edition(first)['handled']==1


def test_known_posting_date_cannot_be_reset_by_republication(setup):
    db,s=setup;ingest(db,s,[job(posted_at='2026-10-01T00:00:00Z')])
    ingest(db,s,[job(posted_at='2026-10-17T15:00:00Z')])
    assert db.jobs()['jobs'][0]['posted_at']=='2026-10-01T00:00:00Z'
    db.publish_edition(END,'2027-06-01',False);assert db.edition()['total']==0


def test_new_discoveries_after_publication_never_change_the_edition(setup):
    db,s=setup;ingest(db,s,[job()]);db.publish_edition(END,'2027-06-01',False)
    ingest(db,s,[job(2)])
    assert db.edition()['total']==1
    with db.connect() as c:c.execute("UPDATE jobs SET description='4 years of experience required.' WHERE canonical_url LIKE '%/1'")
    assert db.edition()['total']==0 and db.edition()['withdrawn']==1


def test_concurrent_publication_creates_one_atomic_edition(setup):
    db,s=setup;ingest(db,s,[job(i) for i in range(10)])
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids=list(pool.map(lambda _:db.publish_edition(END,'2027-06-01',False),range(2)))
    assert ids[0]==ids[1] and len(db.edition()['editions'])==1 and db.edition()['total']==10


def test_630_eastern_cutoff_and_dst():
    assert cutoff_for(END,18,'America/New_York',30)==END
    assert cutoff_for(END-timedelta(seconds=1),18,'America/New_York',30)==END-timedelta(days=1)
    assert next_cutoff(END,18,'America/New_York',30)==END+timedelta(days=1)
    assert cutoff_for(datetime(2026,11,1,23,30,tzinfo=UTC),18,'America/New_York',30).hour==23


def test_seniority_migration_retains_application_history(setup):
    db,s=setup;ingest(db,s,[job(title='Senior Software Engineer I')])
    with db.connect() as c:c.execute("UPDATE jobs SET in_scope=1,status='applied',notes='Keep this history'")
    db.refresh_seniority_rules('2027-06-01')
    row=db.jobs('all')['jobs'][0]
    assert row['in_scope']==0 and row['status']=='applied' and row['notes']=='Keep this history'


def test_publication_api_validates_settings_and_keeps_auth(setup,tmp_path):
    db,s=setup;config=Config(data_dir=tmp_path,password='',secret='x'*40,production=False,schedule=False)
    with TestClient(create_app(config,db)) as client:
        assert client.get('/api/edition').json()['edition'] is None
        for payload in [{'hour':18,'minute':60,'strict_cohort':False},{'hour':18,'minute':30,'strict_cohort':'false'}]:
            assert client.post('/api/publication/preferences',json=payload,headers={'X-Tracker-Request':'1'}).status_code==400
        r=client.post('/api/publication/preferences',json={'hour':18,'minute':30,'strict_cohort':False},headers={'X-Tracker-Request':'1'})
        assert r.status_code==200 and r.json()['preferences']['minute']==30


def test_jobright_public_list_is_discovery_without_inventing_a_timestamp():
    text='''| Company | Job Title | Location | Work Model | Date Posted |
| --- | --- | --- | --- | --- |
| **[Acme](https://example.com)** | **[Software Engineer I](https://jobright.ai/jobs/info/abc?utm_source=test)** | New York, NY, United States | Hybrid | Oct 17 |'''
    rows=parse_github(text,'Jobright public list')
    assert len(rows)==1 and rows[0]['title']=='Software Engineer I'
    assert rows[0]['posted_at'] is None and rows[0]['work_model']=='Hybrid'


def test_smartrecruiters_public_feed_has_employer_details_and_dates():
    requests=[]
    def handle(request):
        requests.append(str(request.url))
        row={'id':'123','name':'Software Engineer I','company':{'name':'Acme'},'releasedDate':'2026-10-17T15:00:00Z',
             'location':{'country':'us','city':'New York','region':'NY'},'typeOfEmployment':{'label':'Full-time'}}
        if request.url.path.endswith('/123'):
            return httpx.Response(200,json={**row,'jobAd':{'sections':{'qualifications':{'text':'0-2 years of experience.'}}},'applyUrl':'https://jobs.smartrecruiters.com/Acme/123'})
        return httpx.Response(200,json={'content':[row],'totalFound':1})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await Fetcher(client).collect({'provider':'smartrecruiters','slug':'Acme','name':'Acme'})
    jobs,_=asyncio.run(run())
    assert len(requests)==4 and jobs[0]['description']=='0-2 years of experience.'
    assert jobs[0]['posted_kind']=='employer_publication' and jobs[0]['country']=='US'
    assert board_from_url(jobs[0]['apply_url'])==('smartrecruiters','Acme')


def test_one_edition_per_local_day_even_if_cutoff_setting_changes(setup):
    db,s=setup;ingest(db,s,[job(i) for i in range(120)])
    first=db.publish_edition(END,'2027-06-01',False)
    assert db.publish_edition(END+timedelta(minutes=30),'2027-06-01',False)==first
    assert db.edition()['edition']['total']==100


def test_employer_metadata_and_contracts_override_weak_title():
    j={**job(),'verified_at':'2026-10-17T17:00:00Z'}
    assert not eligibility({**j,'employer_level':'Mid-Senior level'},strict_cohort=False)['eligible']
    assert not eligibility({**j,'employment_type':'Part-time'},strict_cohort=False)['eligible']
    assert not eligibility({**j,'description':'This is a contract position. No prior experience required.'},strict_cohort=False)['eligible']
    assert not classify('Front End Entry Level','','New York, NY')['in_scope']


def test_dst_edition_follows_the_local_day_without_overlap(setup):
    db,s=setup
    cutoff=datetime(2026,11,1,23,30,tzinfo=UTC)
    db.publish_edition(cutoff,'2027-06-01',False)
    edition=db.edition()['edition']
    assert timestamp(edition['window_start'])==datetime(2026,10,31,22,30,tzinfo=UTC)
    assert timestamp(edition['cutoff'])-timestamp(edition['window_start'])==timedelta(hours=25)


def test_known_old_calendar_date_is_not_reset_by_republishing(setup):
    db,s=setup;ingest(db,s,[job(posted_at='2026-10-01')])
    ingest(db,s,[job(posted_at='2026-10-17T15:00:00Z')])
    assert db.jobs()['jobs'][0]['posted_at']=='2026-10-01'
    db.publish_edition(END,'2027-06-01',False);assert db.edition()['total']==0


@pytest.mark.parametrize('text',['No H-1B sponsorship.','We cannot provide H1B sponsorship.','U.S. person required.'])
def test_explicit_future_sponsorship_and_person_restrictions(text):
    assert classify('Software Engineer I',text,'New York, NY')['sponsorship']=='restricted'
