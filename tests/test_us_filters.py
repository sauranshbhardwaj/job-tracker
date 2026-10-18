import json
from datetime import datetime,timedelta,timezone

import pytest
from fastapi.testclient import TestClient

from tracker.app import create_app
from tracker.config import Config
from tracker.db import Database
from tracker.domain import classify,location_status,us_location_label
from tracker.sources import ashby,lever,recruitee


@pytest.mark.parametrize('location,country,expected',[
    ('Dubai, United Arab Emirates','','outside_us'),('Dubai','US','outside_us'),
    ('Abu Dhabi, UAE','','outside_us'),('Doha, Qatar','','outside_us'),
    ('Remote · Armenia · Serbia · Kazakhstan · Georgia · European Union','','outside_us'),
    ('Georgia','','unknown'),('Tbilisi, Georgia','','outside_us'),('Georgia','GE','outside_us'),
    ('Boston, United Kingdom','','outside_us'),('San Jose, Costa Rica','CR','outside_us'),
    ('Remote','','unknown'),('Worldwide remote','','unknown'),('Atlantis','','unknown'),
    ('Ontario, CA','','unknown'),
    ('London, UK','','outside_us'),('Canada','','outside_us'),
    ('Atlanta, Georgia','','us'),('Georgia','US','us'),('Atlanta, GA','','us'),
    ('Remote - US','','us'),('Remote (U.S.)','','us'),('USA','','us'),('Remote','US','us'),
    ('New York or London','','us'),('Seattle, WA · Dubai, UAE','','us'),
    ('Berlin, NJ','','us'),('Paris, TX','','us'),('United States','','us'),
    ('Remote, excluding United States','','outside_us'),
    ('Non-US remote','','outside_us'),('Remote (not US)','','outside_us'),
])
def test_strict_location_signals(location,country,expected):
    assert location_status(location,country)==expected
    assert bool(classify('Software Engineer','',location,country)['in_scope'])==(expected=='us' or location in {'London, UK','Canada','Boston, United Kingdom'})


def test_title_contradiction_is_not_treated_as_a_us_job():
    title='Metaverse Software Engineer (Remote UK, Singapore, Dubai)'
    result=classify(title,'','United States','US')
    assert result['location_status']=='unknown' and result['in_scope']==0
    assert classify('AI Engineer (UAE)','','United States','US')['in_scope']==0
    assert classify('Software Engineer (US Remote)','','United States','US')['in_scope']==1


@pytest.fixture
def setup(tmp_path):
    config=Config(data_dir=tmp_path,password='',secret='x'*40,production=False,schedule=False)
    config.prepare();db=Database(tmp_path/'test.sqlite3');db.add_source('ashby','acme','Acme')
    return config,db,db.sources()[0]


def posting(id,**kw):
    return dict(external_id=str(id),title=kw.pop('title','Software Engineer'),company='Acme',
                location=kw.pop('location','New York, NY'),country=kw.pop('country','US'),
                description=kw.pop('description','0-2 years of experience.'),
                apply_url=f'https://jobs.ashbyhq.com/acme/{id}',**kw)


def test_existing_records_are_fixed_without_losing_notes_or_status(setup):
    config,db,s=setup
    db.ingest(s,[posting(1,location='Dubai',country=''),posting(2)],None,config.start_date)
    with db.connect() as c:
        c.execute("UPDATE jobs SET location_status='unknown',in_scope=1,status='saved',notes='Keep my notes' WHERE canonical_url LIKE '%/1'")
    db.refresh_location_rules(config.start_date)
    assert db.jobs()['total']==1
    hidden=db.jobs('all',search='Dubai')['jobs'][0]
    assert hidden['location_status']=='outside_us' and hidden['in_scope']==0
    assert hidden['status']=='saved' and hidden['notes']=='Keep my notes'
    assert db.jobs('saved')['total']==0
    assert db.get('us_location_migration')['changed']==1
    db.refresh_location_rules(config.start_date)
    assert db.get('us_location_migration')['changed']==1


def test_feed_saved_and_applications_enforce_us_even_on_legacy_stale_scope(setup):
    config,db,s=setup
    db.ingest(s,[posting(1),posting(2,location='Remote',country=''),posting(3,location='Dubai',country='')],None,config.start_date)
    with db.connect() as c:c.execute("UPDATE jobs SET in_scope=1")
    assert db.jobs()['total']==1
    with db.connect() as c:c.execute("UPDATE jobs SET status='saved'")
    assert db.jobs('saved')['total']==1
    with db.connect() as c:c.execute("UPDATE jobs SET status='applied'")
    assert db.jobs('tracked')['total']==1 and db.jobs('all')['total']==3


def test_filters_combine_and_preserve_unknowns_until_user_filters_them(setup):
    config,db,s=setup
    db.ingest(s,[posting(1,title='Software Engineer, New Grad 2027',work_model='remote',description='0-2 years of experience. Visa sponsorship is available.'),
                 posting(2,title='Data Engineer',work_model='hybrid',description='2 years of experience.'),
                 posting(3,title='QA Engineer',description=''),posting(4,title='Software Engineer',description='3 years of experience.')],None,config.start_date)
    assert db.jobs()['total']==4
    assert db.jobs(role='data',work_model='hybrid',experience='2')['total']==1
    assert db.jobs(role='data',experience='1')['total']==0
    assert db.jobs(experience='unknown')['total']==1
    assert db.jobs(graduate='2027',sponsorship='supported',work_model='remote',verified=True)['total']==1
    assert db.jobs(graduate='2027',role='data')['total']==0
    old=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
    with db.connect() as c:
        c.execute("UPDATE jobs SET first_seen=? WHERE title='Data Engineer'",(old,))
        c.execute("UPDATE jobs SET status='applied' WHERE title='QA Engineer'")
    assert db.jobs(found_days=7)['total']==3
    assert db.jobs(unapplied=True)['total']==3
    assert db.jobs('tracked')['total']==1
    assert db.jobs(role='quality',status='applied',unapplied=True)['total']==1


def test_invalid_api_filters_rejected_and_bookmark_parameters_combine(setup):
    config,db,s=setup
    db.ingest(s,[posting(1,title='Software Engineer, New Grad 2027',work_model='remote')],None,config.start_date)
    with TestClient(create_app(config,db)) as c:
        for query in ['role=evil','found_days=-1','sort=DROP','experience=10','graduate=nope','work_model=everywhere']:
            assert c.get('/api/jobs?'+query).status_code==400
        d=c.get('/api/jobs?role=software&graduate=2027&work_model=remote&found_days=7&verified=true&unapplied=true&sort=newest').json()
        assert d['total']==1 and d['jobs'][0]['work_model']=='remote'


def test_work_arrangement_from_provider_metadata():
    s={'name':'Acme'}
    a=ashby(dict(id='1',title='Engineer',jobUrl='https://example.com',workplaceType='Remote',
                 secondaryLocations=[{'location':'New York','address':{'postalAddress':{'addressCountry':'US'}}}]),s)
    assert a['country']=='US' and a['work_model']=='Remote'
    assert lever(dict(id='1',text='Engineer',hostedUrl='https://example.com',workplaceType='hybrid'),s)['work_model']=='hybrid'
    assert recruitee(dict(id='1',title='Engineer',careers_url='https://example.com',hybrid=True),s)['work_model']=='hybrid'


def test_posted_sort_keeps_missing_dates_last_and_freshness_is_found_date(setup):
    config,db,s=setup
    db.ingest(s,[posting(1,posted_at=None),posting(2,posted_at='2026-10-01'),posting(3,posted_at='2026-10-15')],None,config.start_date)
    jobs=db.jobs(sort='posted')['jobs']
    assert [j['posted_at'] for j in jobs]==['2026-10-15','2026-10-01',None]
    assert db.jobs(found_days=1)['total']==3


def test_compact_locations_show_us_options_and_leave_raw_record_intact(setup):
    config,db,s=setup
    raw='Chicago, IL, United States; Dublin, Ireland; New York, NY, United States'
    db.ingest(s,[posting(1,location=raw,country='')],None,config.start_date)
    row=db.jobs()['jobs'][0]
    assert row['location']==raw and 'Dublin' not in row['us_location']
    assert 'Chicago' in row['us_location'] and 'New York' in row['us_location']
    assert us_location_label('Remote (United States | Canada)')=='US option listed'
