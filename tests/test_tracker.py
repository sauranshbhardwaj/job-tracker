import asyncio
import json
from datetime import datetime,timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from tracker.app import create_app
from tracker.collector import Collector,schedule_due
from tracker.config import Config
from tracker.db import Database
from tracker.domain import canonical_url,classify
from tracker.plus import Plus,PlanError,authorization,exchange
from tracker.sources import Fetcher,board_from_url,parse_github,validate_source


@pytest.fixture
def config(tmp_path):
    c=Config(data_dir=tmp_path,password='',secret='testing-secret-'+('x'*40),production=False,schedule=False)
    c.prepare()
    return c


@pytest.fixture
def db(config): return Database(config.data_dir/'test.sqlite3')


def job(**overrides):
    return dict(external_id='123',title='Software Engineer, New Grad 2027',company='Acme',location='New York, NY',
                country='US',description='0-3 years of experience. Visa sponsorship is available.',
                apply_url='https://jobs.ashbyhq.com/acme/123/apply',posted_at=None,**overrides)


def source(db,provider='ashby',slug='acme'):
    db.add_source(provider,slug,'Acme')
    return next(s for s in db.sources() if s['provider']==provider and s['slug']==slug)


@pytest.mark.parametrize('text',[
    'You must be a U.S. citizen.',
    'US citizenship is required.',
    'U.S. citizens only.',
    'We cannot provide visa sponsorship.',
    'This role does not offer sponsorship.',
    'Applicants must not require sponsorship now or in the future.',
    'Candidates must be a permanent resident.',
    'No visa sponsorship is available.',
])
def test_explicit_restrictions(text):
    result=classify('Software Engineer I',text,'Seattle, WA')
    assert result['sponsorship']=='restricted'
    assert result['evidence'][0]['quote'] in text


@pytest.mark.parametrize('text',[
    'Are you legally authorized to work in the United States?',
    'Do you now or in the future require visa sponsorship?',
    'US citizenship is not required.',
    'We participate in E-Verify.',
    'We have sponsored engineers in the past.',
    'Security clearance eligibility will be reviewed.',
    'The applicant must be a US person or eligible for an export license.',
    'OPT candidates are welcome.',
])
def test_unclear_information_never_becomes_a_promise_or_restriction(text):
    assert classify('Software Engineer',text,'Remote')['sponsorship']=='unknown'


def test_conflicting_evidence_stays_visible():
    result=classify('Software Engineer I','Visa sponsorship is available. We cannot sponsor this position.','New York')
    assert result['sponsorship']=='conflict'
    assert result['in_scope']==1


def test_broad_entry_level_scope():
    for title in ['Data Engineer','Machine Learning Engineer','Backend Developer','SRE','Software Engineer II','QA Engineer']:
        assert classify(title,'0-3 years of professional experience.','Philadelphia, PA')['in_scope']==1
    assert classify('Senior Software Engineer','6 years of experience','New York')['in_scope']==0
    assert classify('Software Engineer','5 years of experience','New York')['in_scope']==0
    assert classify('Software Engineer','5 years of experience or 2 years of experience with a Masters','New York')['in_scope']==1
    assert classify('Software Engineering Intern','0 years of experience','New York')['in_scope']==0
    assert classify('Software Engineer','4 years of experience preferred','New York')['in_scope']==1


def test_location_and_cohort():
    assert classify('Software Engineer','', 'London, UK')['in_scope']==1
    assert classify('Software Engineer','', 'Remote')['location_status']=='unknown'
    assert classify('Software Engineer','', 'New York or London')['in_scope']==1
    assert classify('New Grad Software Engineer 2026','', 'New York')['in_scope']==0
    assert classify('New Grad Software Engineer 2027','', 'New York')['in_scope']==1


def test_canonical_url_deduplicates_tracking_but_preserves_job_identity():
    assert canonical_url('https://jobs.ashbyhq.com/acme/123/apply?utm_source=x')==canonical_url('https://jobs.ashbyhq.com/acme/123')
    assert canonical_url('https://jobs.ashbyhq.com/acme/123/application')==canonical_url('https://jobs.ashbyhq.com/acme/123')
    assert canonical_url('https://example.com/careers?gh_jid=1&source=github').endswith('gh_jid=1')
    assert canonical_url('https://example.com/careers?gh_jid=2')!=canonical_url('https://example.com/careers?gh_jid=1')
    with pytest.raises(ValueError): canonical_url('javascript:alert(1)')


def test_dedup_preserves_user_status_and_employer_description(db):
    s=source(db)
    db.ingest(s,[job()],None,'2027-06-01')
    existing=db.jobs()['jobs'][0]
    with db.connect() as c: c.execute("UPDATE jobs SET status='applied',notes='Recruiter conversation' WHERE id=?",(existing['id'],))
    community=source(db,'github','owner/repo')
    list_job=job();list_job.update(apply_url='https://jobs.ashbyhq.com/acme/123?utm_source=list',description='')
    db.ingest(community,[list_job],None,'2027-06-01')
    records=db.jobs('all')['jobs']
    assert len(records)==1
    assert records[0]['sponsorship']=='supported'
    assert records[0]['status']=='applied'
    assert records[0]['description']==job()['description']
    assert records[0]['posted_at'] is None
    assert len(records[0]['sources'])==2


def test_source_failure_preserves_jobs_and_successful_empty_requires_two_snapshots(db):
    s=source(db)
    db.ingest(s,[job()],None,'2027-06-01')
    db.source_failed(s['id'],None,'HTTP 503')
    assert db.jobs()['total']==1
    db.ingest(s,[],None,'2027-06-01')
    assert db.jobs()['total']==1
    db.ingest(s,[],None,'2027-06-01')
    assert db.jobs('closed')['total']==1
    db.ingest(s,[job()],None,'2027-06-01')
    assert db.jobs()['total']==1


def test_invalid_snapshot_rolls_back_all_mutations(db):
    s=source(db);db.ingest(s,[job()],None,'2027-06-01')
    bad=job();bad['apply_url']='javascript:alert(1)'
    with pytest.raises(ValueError): db.ingest(s,[bad],None,'2027-06-01')
    with db.connect() as c: assert c.execute('SELECT misses FROM sightings').fetchone()[0]==0
    assert db.jobs()['total']==1


def test_stale_list_link_is_closed_only_after_two_successful_board_checks(db):
    community=source(db,'github','owner/repo')
    db.ingest(community,[job()],None,'2027-06-01')
    employer=source(db)
    db.ingest(employer,[],None,'2027-06-01')
    assert db.jobs()['total']==1
    db.ingest(employer,[],None,'2027-06-01')
    assert db.jobs('closed')['total']==1
    db.ingest(employer,[job()],None,'2027-06-01')
    assert db.jobs()['jobs'][0]['availability']=='open'


def test_url_upgrade_merges_legacy_duplicates_without_losing_application_history(db):
    s=source(db)
    db.ingest(s,[job()],None,'2027-06-01')
    with db.connect() as c:
        record=dict(c.execute('SELECT * FROM jobs').fetchone())
        record.update(id='legacy-copy',canonical_url='https://jobs.ashbyhq.com/acme/123/application',status='applied',notes='Already applied')
        c.execute('INSERT INTO jobs('+','.join(record)+') VALUES('+','.join('?' for _ in record)+')',list(record.values()))
    db.set('url_normalization_version',1)
    reopened=Database(db.path)
    assert reopened.jobs('all')['total']==1
    result=reopened.jobs('all')['jobs'][0]
    assert result['status']=='applied' and result['notes']=='Already applied'
    assert result['sponsorship']=='supported'


def test_changed_posting_preserves_revision_and_applied_status(db):
    s=source(db);db.ingest(s,[job()],None,'2027-06-01')
    original=db.jobs()['jobs'][0]
    with db.connect() as c: c.execute("UPDATE jobs SET status='applied' WHERE id=?",(original['id'],))
    changed=job();changed['description']='We cannot offer sponsorship.'
    db.ingest(s,[changed],None,'2027-06-01')
    assert db.jobs()['total']==0
    assert db.jobs('tracked')['jobs'][0]['status']=='applied'
    with db.connect() as c: assert c.execute('SELECT count(*) FROM revisions').fetchone()[0]==2


def test_parser_preserves_uncertain_list_evidence():
    markdown='''| Company | Role | Location | Link | Date |
| --- | --- | --- | --- | --- |
| Acme | Software Engineer 2027 🛂 | New York, NY | [Apply](https://jobs.ashbyhq.com/acme/123) | Oct 1 |
| ↳ | Data Engineer | Seattle, WA | [Apply](https://jobs.lever.co/acme/456) | Oct 2 |
| Other | Software Engineer 🔒 | NY | [Apply](https://example.com/closed) | Oct 1 |
'''
    jobs=parse_github(markdown,'list')
    assert len(jobs)==2 and jobs[1]['company']=='Acme'
    assert classify(jobs[0]['title'],jobs[0]['description'],jobs[0]['location'])['sponsorship']=='unknown'
    assert board_from_url(jobs[0]['apply_url'])==('ashby','acme')
    with pytest.raises(ValueError): parse_github('This page changed.','list')


def test_sources_cannot_become_arbitrary_network_targets():
    for slug in ['../../localhost','http://169.254.169.254','x?url=evil','x/y']:
        with pytest.raises(ValueError): validate_source('ashby',slug)
    assert board_from_url('https://jobs.ashbyhq.com.evil.test/acme/123') is None


def test_collection_rejects_partial_snapshot_and_reports_error(db,config):
    db.add_source('greenhouse','acme','Acme')
    def handler(request): return httpx.Response(200,json={'jobs':[],'meta':{'total':10}})
    collector=Collector(db,config,lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asyncio.run(collector.run())
    run=db.runs()[0]
    assert run['status']=='failed' and run['success_count']==0
    assert 'Incomplete' in db.sources()[0]['last_error']


def test_schedule_catch_up_and_daylight_saving():
    current=datetime(2026,11,1,14,tzinfo=timezone.utc) # 9am EST after fall-back
    assert schedule_due('2026-10-31T12:00:00+00:00',current)
    assert not schedule_due('2026-11-01T13:00:00+00:00',current)
    assert schedule_due(None,current)
    early=datetime(2026,11,2,11,tzinfo=timezone.utc) # before today's 8am
    assert not schedule_due('2026-11-01T13:00:00+00:00',early)


@pytest.mark.parametrize('status',['running','interrupted'])
def test_interrupted_refresh_is_due_again_after_restart(db,config,status):
    db.set('last_schedule_attempt','2026-10-17T13:00:00+00:00')
    with db.connect() as c:
        c.execute('INSERT INTO runs(id,started_at,status) VALUES(?,?,?)',('crashed','2026-10-17T13:00:00+00:00',status))
    Collector(db,config).recover()
    assert db.runs()[0]['status']=='interrupted'
    assert db.get('last_schedule_attempt') is None
    assert schedule_due(db.get('last_schedule_attempt'),datetime(2026,10,17,14,tzinfo=timezone.utc))


def test_app_auth_csrf_and_user_data(config,db):
    config.password='a-long-test-password'
    app=create_app(config,db)
    with TestClient(app) as client:
        assert client.get('/api/jobs').status_code==401
        assert client.post('/api/login',json={'password':config.password}).status_code==403
        headers={'X-Tracker-Request':'1'}
        assert client.post('/api/login',json={'password':config.password},headers=headers).status_code==200
        assert client.get('/api/jobs').status_code==200
        assert client.post('/api/jobs',json={},headers={**headers,'Origin':'https://evil.example'}).status_code==403
        response=client.post('/api/jobs',json={'title':'Software Engineer','company':'Acme','location':'New York, NY','apply_url':'https://example.com/job'},headers=headers)
        assert response.status_code==200
        records=client.get('/api/jobs').json()['jobs']
        assert records[0]['availability']=='unverified'
        assert client.get('/api/export').status_code==200
        assert 'chatgpt_credentials' not in client.get('/api/export').text
        assert client.post('/api/logout',headers=headers).status_code==200
        assert client.get('/api/jobs').status_code==401


def test_production_refuses_unprotected_startup(tmp_path):
    with pytest.raises(RuntimeError): Config(data_dir=tmp_path,password='',secret='',production=True).prepare()


def test_plus_accepts_only_plan_permission_and_encrypts(db,config):
    plus=Plus(db,config)
    with pytest.raises(PlanError): plus.save({'access_token':'sk-test','scopes':['chatgpt.tokens.use.direct']})
    with pytest.raises(PlanError): plus.save({'access_token':'oauth-token','scopes':['openid']})
    record={'access_token':'oauth-private-token','scopes':['chatgpt.tokens.use.direct']}
    plus.save(record)
    assert 'oauth-private-token' not in db.get('chatgpt_credentials')
    assert plus.record()==record


def test_oauth_requires_fresh_state_and_registered_client_id():
    url,pending=authorization('urn:uuid:test','http://127.0.0.1:1455/auth/callback')
    assert 'code_challenge_method=S256' in url and 'dynamic_agent_client' in url
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(500))) as client:
            with pytest.raises(PlanError): await exchange(client,pending,{'state':'wrong'},'urn:uuid:test')
            with pytest.raises(PlanError): await exchange(client,pending,{'state':pending['state'],'code':'x'},'urn:uuid:test')
    asyncio.run(run())


def test_owner_password_minimum_keeps_authentication_and_invalidates_old_sessions(tmp_path):
    secret='x'*40
    with pytest.raises(RuntimeError):
        Config(data_dir=tmp_path,password='testpass',secret=secret,production=True,schedule=False).prepare()
    for password,app_secret in [('',secret),('short',secret),('testpass','too-short')]:
        with pytest.raises(RuntimeError):
            Config(data_dir=tmp_path,password=password,secret=app_secret,production=True,schedule=False,password_min_length=8).prepare()
    with pytest.raises(ValueError):
        Config(data_dir=tmp_path,password='testpass',secret=secret,production=True,schedule=False,password_min_length=1).prepare()
    original=Config(data_dir=tmp_path,password='original-test-password',secret=secret,production=True,schedule=False)
    with TestClient(create_app(original),base_url='https://testserver') as c:
        assert c.post('/api/login',json={'password':original.password},headers={'X-Tracker-Request':'1'}).status_code==200
        cookie=c.cookies.get('tracker_session')
    changed=Config(data_dir=tmp_path,password='testpass',secret=secret,production=True,schedule=False,password_min_length=8)
    with TestClient(create_app(changed),base_url='https://testserver') as c:
        assert c.get('/api/edition').status_code==401
        assert c.get('/api/edition',headers={'Cookie':'tracker_session='+cookie}).status_code==401
        assert c.post('/api/login',json={'password':original.password},headers={'X-Tracker-Request':'1'}).status_code==401
        assert c.post('/api/login',json={'password':changed.password},headers={'X-Tracker-Request':'1'}).status_code==200
        assert c.get('/api/edition').status_code==200
