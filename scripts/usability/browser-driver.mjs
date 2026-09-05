// Isolated browser operator for scenario agents. It exposes UI actions, not API calls.
import http from 'node:http';
import { mkdir, appendFile, writeFile, readFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const root = process.cwd();
const runtime = process.env.USABILITY_PLAYWRIGHT;
if (!runtime) throw new Error('Set USABILITY_PLAYWRIGHT to the installed playwright module.');
const { chromium } = await import(pathToFileURL(runtime));
const browser = await chromium.launch({ headless: true, ...(process.env.USABILITY_CHROMIUM ? { executablePath: process.env.USABILITY_CHROMIUM } : {}) });
const base = 'http://127.0.0.1:5180';
const output = path.join(root, '.cache/usability/browser');
await mkdir(output, {recursive:true, mode:0o700});
const actors = new Map();
const safeName = value => /^[a-z0-9_-]+$/.test(value);
const allowedHosts = new Set(['127.0.0.1:5180','localhost:5180','127.0.0.1:19010']);

async function record(a, value) {
  await appendFile(path.join(a.dir,'events.jsonl'),JSON.stringify({at:new Date().toISOString(),...value})+'\n');
}
async function snapshot(a) {
  const p=a.page;
  await p.waitForTimeout(200);
  a.controls = new Map();
  const els = await p.locator('a, button, input, textarea, select, summary, [role="button"], [role="tab"]').elementHandles();
  const controls=[];
  for (const el of els) {
    let proxy=null;
    if (!await el.isVisible().catch(()=>false)) {
      const handle=await el.evaluateHandle(e=>e instanceof HTMLInputElement && ['checkbox','radio'].includes(e.type) ? e.labels?.[0] ?? null : null);
      proxy=handle.asElement();
      if(!proxy || !await proxy.isVisible())continue;
    }
    const id=controls.length+1;
    const view=await el.evaluate(e=>({tag:e.tagName.toLowerCase(),type:e.getAttribute('type'),role:e.getAttribute('role'),text:(e.innerText||'').trim().slice(0,240),label:e.getAttribute('aria-label')||Array.from(e.labels||[]).map(l=>l.innerText).join(' '),placeholder:e.getAttribute('placeholder'),value:e.value,checked:e.checked,disabled:e.disabled||e.getAttribute('aria-disabled')==='true',href:e.tagName==='A'?e.getAttribute('href'):undefined,options:e.tagName==='SELECT'?Array.from(e.options).map(o=>({value:o.value,label:o.text,disabled:o.disabled})):undefined}));
    a.controls.set(id,{el,proxy}); controls.push({id,...view});
  }
  const bounds=await p.evaluate(()=>({viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,scrollY,viewportHeight:innerHeight}));
  const state={url:p.url(),title:await p.title(),bounds,text:(await p.locator('body').innerText()).slice(0,25000),controls,downloads:a.downloads.map(d=>({name:d.name,path:d.path})),fileChooserOpen:!!a.chooser,tabs:a.context.pages().map((t,i)=>({id:i,url:t.url(),current:t===p}))};
  await writeFile(path.join(a.dir,'latest.json'),JSON.stringify(state,null,2));
  return state;
}
function attach(a,p) {
  p.on('filechooser',chooser=>{a.chooser=chooser;});
  p.on('dialog',async dialog=>{await record(a,{kind:'dialog',type:dialog.type(),message:dialog.message()});await dialog.dismiss();});
  p.on('pageerror',error=>{void record(a,{kind:'pageerror',message:String(error)});});
  p.on('response',res=>{
    const u=new URL(res.url());
    if (u.pathname.startsWith('/api/')) void record(a,{kind:'api',method:res.request().method(),path:u.pathname,status:res.status()});
  });
  p.on('download',async download=>{
    const name=download.suggestedFilename();
    const filename=path.join(a.dir,`download-${a.downloads.length}-${path.basename(name)}`);
    await download.saveAs(filename);a.downloads.push({name,path:filename});
    await record(a,{kind:'download',name,path:filename});
  });
}
async function execute(body) {
  const {actor,action}=body;
  if (!safeName(actor)) throw new Error('Invalid actor name');
  if(action==='start') {
    if(actors.has(actor)) throw new Error('Actor already started');
    if(!['student-1','student-2','reviewer-1','reviewer-2','coordinator'].includes(body.identity)) throw new Error('Unknown fixture identity');
    const context=await browser.newContext({viewport:{width:body.width||1440,height:900},acceptDownloads:true,locale:'ru-RU'});
    await context.route('**/*',async route=>{
      const u=new URL(route.request().url());
      if(['data:','blob:'].includes(u.protocol)||allowedHosts.has(u.host)||['fonts.googleapis.com','fonts.gstatic.com'].includes(u.hostname)) await route.continue();
      else await route.abort('blockedbyclient');
    });
    const login=await context.request.post(base+'/api/v1/auth/local/login',{data:{identity:body.identity}});
    if(!login.ok()) throw new Error(`Fixture login failed ${login.status()}`);
    const dir=path.join(output,actor);await mkdir(dir,{recursive:true,mode:0o700});
    const a={context,page:await context.newPage(),dir,controls:new Map(),downloads:[],chooser:null,step:0,readonly:!!body.readonly};
    actors.set(actor,a);
    if(a.readonly) await context.route('**/api/**',async route=>{
      if(['GET','HEAD','OPTIONS'].includes(route.request().method())) await route.continue();
      else await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({code:'audit_readonly',message:'Редакторский проход без изменения данных.',action:null})});
    });
    context.on('page',p=>attach(a,p));attach(a,a.page);
    await a.page.goto(base+'/#/home');await a.page.waitForTimeout(1300);
    await record(a,{kind:'start',identity:body.identity,readonly:a.readonly,viewport:body.width||1440});
    return snapshot(a);
  }
  const a=actors.get(actor);if(!a) throw new Error('Start this actor first');
  const p=a.page;
  if(action==='close'){await a.context.close();actors.delete(actor);return {closed:true};}
  if(action==='snapshot') return snapshot(a);
  if(action==='screenshot') {
    const file=path.join(a.dir,`${String(++a.step).padStart(3,'0')}-${safeName(body.name||'screen')?body.name||'screen':'screen'}.png`);
    await p.screenshot({path:file,fullPage:body.fullPage!==false});await record(a,{kind:'screenshot',file});return {file};
  }
  if(action==='switch_tab') {const tab=a.context.pages()[body.id];if(!tab)throw new Error('Unknown tab');a.page=tab;}
  else if(action==='back') await p.goBack();
  else if(action==='reload') await p.reload();
  else if(action==='wait') await p.waitForTimeout(Math.min(5000,body.ms||1000));
  else if(action==='scroll') await p.mouse.wheel(body.x||0,body.y||600);
  else if(action==='viewport') await p.setViewportSize({width:body.width,height:body.height||900});
  else if(action==='key') await p.keyboard.press(body.key);
  else if(action==='upload') {
    const filename=path.resolve(root,'scripts/usability/fixtures',body.file);
    if(path.dirname(filename)!==path.resolve(root,'scripts/usability/fixtures'))throw new Error('Only provided study fixtures are allowed');
    if(!a.chooser)throw new Error('Open the file chooser through the interface first');
    await a.chooser.setFiles(filename);a.chooser=null;
  }
  else if(action==='read_download') {
    const d=a.downloads[body.id||0];if(!d)throw new Error('No such download');
    if(!/\.(md|txt|csv)$/i.test(d.name))return {name:d.name,note:'Binary download; use the file artifact to inspect it.'};
    return {name:d.name,text:(await readFile(d.path,'utf8')).slice(0,30000)};
  }
  else {
    const control=a.controls.get(body.id);if(!control)throw new Error('Take a fresh snapshot and choose its control id');
    const {el,proxy}=control;
    if(action==='click')await (proxy||el).click({timeout:8000});
    else if(action==='fill')await el.fill(body.value,{timeout:8000});
    else if(action==='select')await el.selectOption(body.value);
    else if(action==='check') {
      if(proxy){if(await el.isChecked()!==(body.value!==false))await proxy.click();}
      else await el.setChecked(body.value!==false);
    }
    else throw new Error('Unknown UI action');
  }
  await record(a,{kind:'action',action,id:body.id,value:body.value,file:body.file,key:body.key});
  await a.page.waitForTimeout(450);
  return snapshot(a);
}
const server=http.createServer(async(req,res)=>{
  if(req.method!=='POST'){res.writeHead(405);res.end();return;}
  try{let data='';for await(const chunk of req){data+=chunk;if(data.length>100000)throw new Error('Request too large');}
    const result=await execute(JSON.parse(data));res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(result));
  }catch(error){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({error:String(error)}));}
});
server.listen(18180,'127.0.0.1',()=>console.log('UI driver ready on loopback 18180'));
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,async()=>{await browser.close();server.close(()=>process.exit(0));});
