"""Geography, authorization, migration and atomic multi-country collection regressions."""
import asyncio
import json
from datetime import datetime,timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from tracker.app import create_app
from tracker.config import Config
from tracker.db import Database
from tracker.domain import classify,market_location_status,market_countries
from tracker.sources import Fetcher,ashby,recruitee,aggregate_job


@pytest.mark.parametrize('location,country,expected',[
    ('London, on-site','','outside_us'),('Toronto, India','','outside_us'),('Waterloo, Belgium','','outside_us'),('London, Ontario','','ca'),
    ('New York, NY','','us'),('Toronto, Canada','','ca'),('Vancouver, BC','','ca'),
    ('Montréal, Quebec','','ca'),('London, ON','','ca'),('London, England','','uk'),
    ('Remote','CA','ca'),('Remote','GB','uk'),('London','GB','uk'),('Edinburgh','','uk'),
    ('London','','outside_us'),('Ontario, CA','','unknown'),('Georgia','','unknown'),
    ('Dubai, UAE','','outside_us'),('Dubai','US','unknown'),('London, UK','US','unknown'),
    ('San Jose, Costa Rica','CR','outside_us'),('Paris, TX','','us'),('London, KY','','us'),
    ('Remote, excluding Canada','','outside_us'),('Remote (not UK)','','outside_us'),
    ('Toronto, Canada · London, UK · New York, US','','us_ca_uk'),
    ('United States, Canada','','us_ca'),('Berlin, Germany · London, UK','','uk'),
])
def test_supported_market_signals(location,country,expected):
    assert market_location_status(location,country)==expected


@pytest.mark.parametrize('location,country', [('Toronto, Canada','CA'),('London, UK','GB')])
@pytest.mark.parametrize('description,expected',[
    ('Visa sponsorship is available.','supported'),('0-2 years of experience.','unknown'),
    ('We do not sponsor visas.','restricted'),('No visa sponsorship available.','restricted'),
    ('Applicants must already have the right to work in Canada or the UK.','restricted'),
    ('You must have unrestricted right to work in Canada or the UK.','restricted'),
    ('You must be eligible to work in the UK.','restricted'),
    ('You must be a Canadian citizen.','restricted'),('British citizens only.','restricted'),
])
def test_international_authorization(location,country,description,expected):
    assert classify('Graduate Software Engineer',description,location,country)['sponsorship']==expected


def posting(i,location,country):
    return dict(external_id=str(i),title=f'Graduate Software Engineer, Team {i}',company='Acme',
                location=location,country=country,description='0-2 years of experience.',
                apply_url=f'https://jobs.ashbyhq.com/acme/{i}',posted_at='2026-10-17T15:00:00Z',
                posted_kind='employer_publication')


def test_combined_cap_filters_migration_and_history(tmp_path):
    db=Database(tmp_path/'test.sqlite3');db.add_source('ashby','acme','Acme');source=db.sources()[0]
    places=[('New York, NY','US'),('Toronto, Canada','CA'),('London, UK','GB')]
    db.ingest(source,[posting(i,*places[i%3]) for i in range(125)],None,'2027-06-01')
    with db.connect() as c:
        c.execute("UPDATE jobs SET in_scope=0,location_status='outside_us' WHERE country!='US'")
        c.execute("UPDATE jobs SET status='applied',notes='Preserve history',ai_review=? WHERE canonical_url LIKE '%/1'",(json.dumps({'note':'Keep advice'}),))
        c.execute("UPDATE jobs SET first_seen='2026-10-17T17:00:00Z',verified_at='2026-10-17T17:00:00Z'")
    db.refresh_market_rules('2027-06-01');db.refresh_market_rules('2027-06-01')
    assert db.get('market_migration')['reviewed']==125
    assert db.jobs(country='ca')['total']==42
    row=db.jobs('tracked',country='ca')['jobs'][0]
    assert row['notes']=='Preserve history' and row['ai_review']['note']=='Keep advice'
    assert row['countries']==['ca']
    first=db.publish_edition(datetime(2026,10,17,22,30,tzinfo=timezone.utc),'2027-06-01',False)
    edition=db.edition();assert edition['edition']['total']==100 and edition['edition']['limit']==100
    assert {c for j in edition['jobs'] for c in j['countries']}=={'us','ca','uk'}
    db.decide(edition['jobs'][0]['id'],{'status':'applied'})
    assert db.edition()['total']==99
    assert db.publish_edition(datetime(2026,10,17,22,30,tzinfo=timezone.utc),'2027-06-01',False)==first
    config=Config(data_dir=tmp_path,password='',secret='x'*40,production=False,schedule=False)
    with TestClient(create_app(config,db)) as client:
        for country in ('ca','uk','us'):
            data=client.get('/api/jobs',params={'country':country}).json()
            assert data['total'] and all(country in j['countries'] for j in data['jobs'])
        assert client.get('/api/jobs?country=ae').status_code==400
        assert client.get('/api/edition').json()['limit']==100


def test_historical_edition_keeps_original_limit(tmp_path):
    db=Database(tmp_path/'test.sqlite3')
    with db.connect() as c:
        c.execute('INSERT INTO editions VALUES(?,?,?,?,?,?,?,?)',('old','2026-10-17T22:30:00Z','2026-10-16T22:30:00Z','2026-10-17T22:30:15Z',1,0,0,'{}'))
    assert db.edition()['edition']['limit']==50


def test_provider_multicountry_metadata():
    source={'name':'Acme'}
    job=ashby(dict(title='Graduate Software Engineer',jobUrl='https://example.com/job',location='Berlin',
        address={'postalAddress':{'addressCountry':'DE'}},secondaryLocations=[
            {'location':'London','address':{'postalAddress':{'addressCountry':'GB'}}},
            {'location':'Toronto','address':{'postalAddress':{'addressCountry':'CA'}}}]),source)
    assert market_countries(market_location_status(job['location'],job['country']))==['ca','uk']
    job=recruitee(dict(id=1,title='Graduate Software Engineer',careers_url='https://example.com/job',
        locations=[{'city':'London','country_code':'GB'},{'city':'Toronto','country_code':'CA'}]),source)
    assert market_location_status(job['location'],job['country'])=='ca_uk'
    job=aggregate_job(dict(guid='https://example.com/job',title='Graduate Software Engineer',companyName='Acme',locationRestrictions=['US','CA','GB']),'himalayas')
    assert market_location_status(job['location'],job['country'])=='us_ca_uk'


@pytest.mark.parametrize('provider',['smartrecruiters','himalayas'])
def test_collectors_query_three_countries_and_deduplicate(provider):
    requested=[]
    def handle(request):
        country=request.url.params.get('country');requested.append(country)
        if provider=='smartrecruiters':
            row=dict(id='123',name='Senior Software Engineer',location={'country':country},company={'name':'Acme'})
            return httpx.Response(200,json={'content':[row],'totalFound':1})
        jobs=[] if request.url.params.get('page')=='2' else [dict(guid='https://example.com/job',title='Graduate Software Engineer',companyName='Acme',locationRestrictions=['US','CA','GB'])]
        return httpx.Response(200,json={'jobs':jobs})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await Fetcher(client).collect({'provider':provider,'slug':'Acme' if provider=='smartrecruiters' else 'us-engineering','name':'Acme'})
    jobs,_=asyncio.run(run())
    assert len(jobs)==1 and {c.lower() for c in requested}=={'us','ca','gb'}


@pytest.mark.parametrize('provider',['smartrecruiters','himalayas'])
def test_country_failure_rejects_whole_snapshot(provider):
    def handle(request):
        country=request.url.params.get('country')
        if country.lower()=='ca':return httpx.Response(200,json={'error':'incomplete'})
        return httpx.Response(200,json={'content':[],'totalFound':0} if provider=='smartrecruiters' else {'jobs':[]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await Fetcher(client).collect({'provider':provider,'slug':'Acme' if provider=='smartrecruiters' else 'us-engineering','name':'Acme'})
    with pytest.raises(ValueError):asyncio.run(run())
