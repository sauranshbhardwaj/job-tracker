import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles

from .collector import Collector
from .config import config as default_config
from .db import Database,now
from .domain import canonical_url,ROLE_FAMILIES
from .editions import cutoff_for,next_cutoff,CAP
from .plus import PlanError,Plus,verify_identity
from .sources import load_seeds,validate_source

STATIC=Path(__file__).resolve().parent.parent/'static'
STATUSES={'new','saved','applied','interview','offer','rejected','dismissed'}


def create_app(config=default_config,db=None):
    config.prepare()
    db=db or Database(config.data_dir/'tracker.sqlite3')
    db.refresh_location_rules(config.start_date)
    db.refresh_seniority_rules(config.start_date)
    db.refresh_market_rules(config.start_date)
    load_seeds(db)
    collector=Collector(db,config)
    plus=Plus(db,config)
    tasks=set()
    login_attempts={}

    def publication_preferences():
        return db.get('publication_preferences',{'hour':config.publication_hour,'minute':config.publication_minute,'strict_cohort':config.strict_cohort})

    def edition_info(edition_id='', summary=False):
        prefs=publication_preferences()
        return {**db.edition(edition_id,config.start_date,summary),'preferences':prefs,'timezone':config.timezone,
                'next_cutoff':next_cutoff(datetime.now(timezone.utc),prefs['hour'],config.timezone,prefs.get('minute',0)).isoformat(),
                'limit':CAP}

    async def publish_loop():
        if not db.get('editions_enabled_at'):db.set('editions_enabled_at',now())
        while True:
            try:
                prefs=publication_preferences()
                cutoff=cutoff_for(datetime.now(timezone.utc),prefs['hour'],config.timezone,prefs.get('minute',0))
                # Do not invent an edition from before this feature was activated.
                if cutoff>=datetime.fromisoformat(db.get('editions_enabled_at')):
                    await asyncio.to_thread(db.publish_edition,cutoff,config.start_date,prefs['strict_cohort'],config.timezone)
                    if db.get('edition_error'):db.set('edition_error',None)
            except asyncio.CancelledError:raise
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Daily edition publication failed')
                db.set('edition_error',{'at':now(),'message':'Publication failed; retrying automatically. Existing editions preserved.'})
            await asyncio.sleep(30)

    def spawn(coro):
        task=asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def review_safely(limit=None):
        try:
            await plus.review(limit)
        except PlanError as exc:
            db.set('chatgpt_last_result',{'at':now(),'status':'paused','message':str(exc)})

    async def refresh():
        result=await collector.run()
        if result and plus.record() and db.get('chatgpt_model'):
            await review_safely()

    @asynccontextmanager
    async def lifespan(app):
        collector.recover()
        if config.schedule:
            async def scheduled_review():
                if plus.record() and db.get('chatgpt_model'):
                    await review_safely()
            spawn(collector.loop(scheduled_review))
            spawn(publish_loop())
        yield
        for task in list(tasks): task.cancel()
        await asyncio.gather(*list(tasks),return_exceptions=True)

    app=FastAPI(title='Open Job Tracker',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.db,app.state.collector,app.state.plus=db,collector,plus
    signing_key=hashlib.sha256((config.secret+config.password).encode()).digest()

    def signature(payload):
        return hmac.new(signing_key,payload.encode(),hashlib.sha256).hexdigest()

    def authenticated(request):
        if not config.password: return True
        cookie=request.cookies.get('tracker_session','')
        try:
            payload,sig=cookie.rsplit('.',1)
            return hmac.compare_digest(signature(payload),sig) and abs(time.time()-float(payload))<86400
        except (ValueError,TypeError): return False

    @app.middleware('http')
    async def protect(request,call_next):
        if request.method in {'POST','PATCH','DELETE','PUT'}:
            if request.headers.get('x-tracker-request')!='1':
                return JSONResponse({'detail':'Same-origin request header required'},status_code=403)
            origin=request.headers.get('origin')
            from urllib.parse import urlsplit
            if origin and urlsplit(origin).netloc!=request.headers.get('host'):
                return JSONResponse({'detail':'Cross-origin writes are not allowed'},status_code=403)
            if int(request.headers.get('content-length','0') or 0)>100_000:
                return JSONResponse({'detail':'Request too large'},status_code=413)
        host=request.url.hostname
        if not config.production and host not in {'localhost','127.0.0.1','::1','testserver'}:
            return JSONResponse({'detail':'Local preview accepts loopback hosts only'},status_code=400)
        if request.url.path.startswith('/api/') and request.url.path not in {'/api/login','/api/session'} and not authenticated(request):
            return JSONResponse({'detail':'Sign in to your tracker'},status_code=401)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['Cache-Control']='no-store'
        return response

    async def payload(request):
        raw=await request.body()
        if len(raw)>100_000: raise HTTPException(413,'Request too large')
        try:
            data=json.loads(raw)
            if not isinstance(data,dict): raise ValueError()
            return data
        except (ValueError,UnicodeDecodeError): raise HTTPException(400,'Expected a JSON object')

    @app.exception_handler(PlanError)
    async def plan_error(request,exc):
        return JSONResponse({'detail':str(exc)},status_code=409)

    @app.get('/healthz')
    def health():
        with db.connect() as c: c.execute('SELECT 1')
        return {'status':'ok'}

    @app.get('/')
    def index(): return FileResponse(STATIC/'index.html')

    app.mount('/static',StaticFiles(directory=STATIC),name='static')

    @app.get('/api/session')
    def session(request:Request):
        return {'authenticated':authenticated(request),'password_required':bool(config.password)}

    @app.post('/api/login')
    async def login(request:Request):
        key=request.client.host if request.client else 'unknown'
        recent=[t for t in login_attempts.get(key,[]) if time.time()-t<300]
        if len(recent)>=10: raise HTTPException(429,'Too many attempts. Try again in five minutes.')
        data=await payload(request)
        if not secrets.compare_digest(str(data.get('password','')),config.password):
            if len(login_attempts)>1000: login_attempts.clear()
            login_attempts[key]=recent+[time.time()]
            raise HTTPException(401,'Incorrect password')
        login_attempts.pop(key,None)
        value=str(time.time())
        response=JSONResponse({'ok':True})
        response.set_cookie('tracker_session',value+'.'+signature(value),max_age=86400,httponly=True,secure=config.production,samesite='strict')
        return response

    @app.post('/api/logout')
    def logout():
        r=JSONResponse({'ok':True}); r.delete_cookie('tracker_session'); return r

    @app.get('/api/jobs')
    def jobs(view:str='feed',search:str='',status:str='',sponsorship:str='',offset:int=0,
             role:str='',work_model:str='',found_days:int=0,experience:str='',graduate:str='',
             verified:bool=False,sort:str='recommended',unapplied:bool=False,country:str=''):
        if (view not in {'feed','saved','tracked','excluded','closed','all'} or status and status not in STATUSES
            or country not in {'','us','ca','uk'}
            or sponsorship not in {'','supported','unknown','conflict','restricted','review'}
            or role and role not in ROLE_FAMILIES or work_model not in {'','remote','hybrid','onsite','unknown'}
            or found_days not in {0,1,3,7,30} or experience not in {'','0','1','2','3','unknown'}
            or graduate not in {'','early','2027'} or sort not in {'recommended','newest','company','posted'}):
            raise HTTPException(400,'Invalid filter')
        return db.jobs(view,search[:200],status,sponsorship,100,max(offset,0),role=role,work_model=work_model,
                       found_days=found_days,experience=experience,graduate=graduate,verified=verified,sort=sort,unapplied=unapplied,country=country)

    @app.patch('/api/jobs/{job_id}')
    async def update_job(job_id:str,request:Request):
        data=await payload(request)
        updates={k:v for k,v in data.items() if k in {'status','notes'}}
        if not updates or ('status' in updates and updates['status'] not in STATUSES): raise HTTPException(400,'Invalid status')
        if 'notes' in updates and (not isinstance(updates['notes'],str) or len(updates['notes'])>10000): raise HTTPException(400,'Notes must be at most 10000 characters')
        if not db.decide(job_id,updates):raise HTTPException(404,'Job not found')
        return {'ok':True}

    @app.get('/api/edition')
    def edition(edition_id:str=''):
        if len(edition_id)>50:raise HTTPException(400,'Invalid edition')
        return edition_info(edition_id)

    @app.post('/api/publication/preferences')
    async def set_publication_preferences(request:Request):
        data=await payload(request)
        if type(data.get('hour')) is not int or not 0<=data['hour']<=23 or type(data.get('minute')) is not int or not 0<=data['minute']<=59 or type(data.get('strict_cohort')) is not bool:
            raise HTTPException(400,'Choose a cutoff hour and cohort policy')
        db.set('publication_preferences',{'hour':data['hour'],'minute':data['minute'],'strict_cohort':data['strict_cohort']})
        return edition_info()

    @app.post('/api/jobs')
    async def add_job(request:Request):
        data=await payload(request)
        try:
            for k in ('title','company','apply_url'):
                if not isinstance(data.get(k),str) or not data[k].strip() or len(data[k])>2000: raise ValueError('Title, company, and application URL are required')
            canonical_url(data['apply_url'])
        except ValueError as exc: raise HTTPException(400,str(exc))
        source_id=db.add_source('manual','user','Manually added')
        source={'id':source_id,'provider':'manual'}
        data.update(external_id=data['apply_url'],description='',location=str(data.get('location',''))[:1000])
        # Manual entries are never presented as employer-verified.
        db.ingest(source,[data],None,config.start_date)
        from .sources import board_from_url
        board=board_from_url(data['apply_url'])
        if board: db.add_source(*board,data['company'],'manual')
        return {'ok':True}

    @app.get('/api/overview')
    def overview():
        runs=db.runs()
        local=datetime.now(timezone.utc).astimezone(ZoneInfo(config.timezone))
        next_run=local.replace(hour=config.hour,minute=0,second=0,microsecond=0)
        if local>=next_run: next_run+=timedelta(days=1)
        summary=db.source_summary()
        return {'runs':runs,'collecting':collector.lock.locked(),'source_count':summary['enabled'],
                'failed_sources':summary['failed'],'coverage':summary,
                'last_complete_refresh':db.get('last_complete_refresh'),'schedule_enabled':config.schedule,
                'next_run':next_run.isoformat(),'start_date':config.start_date,
                'publication':edition_info(summary=True),'edition_error':db.get('edition_error'),
                'plus':plus.status()}

    @app.post('/api/refresh',status_code=202)
    async def refresh_now():
        if collector.lock.locked(): raise HTTPException(409,'A refresh is already running')
        spawn(refresh())
        return {'ok':True}

    @app.get('/api/sources')
    def sources(search:str='',health:str='',offset:int=0,limit:int=100):
        if offset<0 or not 1<=limit<=500: raise HTTPException(400,'Invalid source page')
        return {**db.source_page(search[:200],health,offset,limit),'summary':db.source_summary(),'host_id':db.get('host_id'),
                'unsupported':[{'name':'LinkedIn Jobs','reason':'Automated scraping prohibited; add discovered application links manually.'},
                               {'name':'Jobright','reason':'Public GitHub lists provide discovery only. Its search website is not collected.'}]}

    @app.post('/api/sources')
    async def add_source(request:Request):
        data=await payload(request)
        try:
            provider,slug=str(data.get('provider','')),str(data.get('slug','')).strip()
            validate_source(provider,slug)
            name=str(data.get('name','')).strip() or slug
            if len(name)>150: raise ValueError('Name must be at most 150 characters')
            source_id=db.add_source(provider,slug,name,'user')
        except ValueError as exc: raise HTTPException(400,str(exc))
        return {'id':source_id}

    @app.patch('/api/sources/{source_id}')
    async def toggle_source(source_id:int,request:Request):
        data=await payload(request)
        if not isinstance(data.get('enabled'),bool): raise HTTPException(400,'Expected enabled: true or false')
        with db.connect() as c:
            result=c.execute('UPDATE sources SET enabled=? WHERE id=?',(int(data['enabled']),source_id))
            if not result.rowcount: raise HTTPException(404,'Source not found')
        return {'ok':True}

    @app.get('/api/chatgpt/models')
    async def models(): return {'models':await plus.models()}

    @app.post('/api/chatgpt/import')
    async def import_credentials(request:Request):
        if plus.lock.locked(): raise HTTPException(409,'Wait for the current ChatGPT request to finish')
        data=await payload(request)
        record=data.get('credentials',{})
        if data.get('plan_only_confirmed') is not True:
            raise HTTPException(400,'Confirm that additional-credit fallback is disabled in ChatGPT settings')
        if not isinstance(record,dict) or record.get('ext_agent_host_id')!=db.get('host_id'):
            raise HTTPException(400,'Use this deployment’s host ID when connecting locally')
        if not all(record.get(k) for k in ('client_id','id_token','access_token','refresh_token','scopes')):
            raise HTTPException(400,'Incomplete connection file')
        try:
            async with plus.lock, httpx.AsyncClient(timeout=30) as client:
                claims=await verify_identity(client,record)
                if claims['sub']!=record.get('subject'):
                    raise ValueError('Identity mismatch')
                record['email']=claims.get('email','')
                plus.save(record)
                db.set('plan_only_confirmed',True)
                db.set('chatgpt_model',None)
        except Exception:
            raise HTTPException(400,'Could not validate the OpenAI connection. Run the local sign-in helper again.') from None
        return {'ok':True}

    @app.post('/api/chatgpt/model')
    async def choose_model(request:Request):
        data=await payload(request)
        models=await plus.models()
        if data.get('model') not in {m['id'] for m in models}: raise HTTPException(400,'Choose a model from your account catalog')
        db.set('chatgpt_model',data['model'])
        return {'ok':True}

    @app.post('/api/chatgpt/review',status_code=202)
    async def review(request:Request):
        if plus.lock.locked(): raise HTTPException(409,'A ChatGPT request is already running')
        if not plus.record() or not db.get('chatgpt_model'): raise HTTPException(409,'Connect Plus and choose a model first')
        data=await payload(request) if await request.body() else {}
        limit=data.get('limit')
        if limit is not None and (type(limit) is not int or not 1<=limit<=config.ai_limit):
            raise HTTPException(400,'Review limit must be a positive integer within the daily cap')
        spawn(review_safely(limit))
        return {'ok':True}

    @app.post('/api/chatgpt/disconnect')
    async def disconnect():
        revoked=await plus.disconnect()
        return {'ok':True,'revoked':revoked,'message':'Disconnected.' if revoked else 'Disconnected locally. Remote revocation was not confirmed; disconnect Open Job Tracker in ChatGPT settings.'}

    @app.get('/api/export')
    def export():
        # Credential and account settings are deliberately excluded.
        with db.connect() as c:
            data={'exported_at':now(),'jobs':[dict(r) for r in c.execute('SELECT * FROM jobs')],
                  'sources':db.sources(),'runs':db.runs()}
            data['editions']=[dict(r) for r in c.execute('SELECT * FROM editions')]
            data['edition_items']=[dict(r) for r in c.execute('SELECT * FROM edition_items')]
            data['job_decisions']=[dict(r) for r in c.execute('SELECT * FROM job_decisions')]
        return Response(json.dumps(data,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="jobs-backup.json"'})

    return app


app=create_app()
