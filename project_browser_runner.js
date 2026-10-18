'use strict';
const fs=require('node:fs'),path=require('node:path'),readline=require('node:readline'),crypto=require('node:crypto');
const {chromium}=require('./runtime/assistant/node_modules/playwright');
let browser,context,page,origin,stateKey,consoleRows=[],networkRows=[],assertions=[],dialogs=[],dialogCount=0;
const keep=(array,value)=>{array.push(value);if(array.length>100)array.shift();};
const cleanUrl=value=>{try{const u=new URL(value);return u.origin+u.pathname;}catch{return String(value).slice(0,300);}};
function allowedRequest(url){try{const u=new URL(url);return u.origin===origin;}catch{return false;}}
async function snapshot(){
 const fields=await page.locator('body').evaluate(root=>Array.from(root.querySelectorAll('input,select,textarea')).slice(0,256).map(el=>({id:el.id,name:el.name,type:el.type,value:String(el.value).slice(0,10000),checked:!!el.checked,selected:el.selectedOptions?Array.from(el.selectedOptions).map(v=>v.index):undefined})));
 const signature=crypto.createHmac('sha256',stateKey).update(JSON.stringify(fields)).digest('hex');
 return {url:cleanUrl(page.url()),title:await page.title(),snapshot:(await page.locator('body').ariaSnapshot()).slice(0,10000),text:(await page.locator('body').innerText()).slice(0,6000),state_signature:signature,console:consoleRows.slice(-25),network:networkRows.slice(-35),assertions:assertions.slice(-30),dialog_count:dialogCount,dialogs:dialogs.slice(-25),diagnostics:dialogCount>1?[{code:'multiple_dialogs',severity:'warning',count:dialogCount,message:'One interaction emitted multiple dialogs. Check for duplicate event handlers; assert dialog_count immediately after the action.'}]:[]};
}
async function execute(v){
 if(!['assert','snapshot','screenshot'].includes(v.action)){dialogs=[];dialogCount=0;}
 if(v.action==='open'){
  if(browser)await browser.close();origin=new URL(v.url).origin;
  stateKey=/^[a-f0-9]{32}$/.test(v.state_key||'')?v.state_key:crypto.randomBytes(16).toString('hex');
  if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin))throw Error('Only the owned project preview is allowed');
  consoleRows=[];networkRows=[];assertions=[];
  browser=await chromium.launch({headless:true,args:['--disable-gpu'],timeout:20000});context=await browser.newContext({viewport:{width:1280,height:800},acceptDownloads:false,serviceWorkers:'block'});
  await context.route('**/*',async route=>{const r=route.request();if(!allowedRequest(r.url())||(new URL(r.url()).origin!==origin&&r.method()!=='GET')||(r.isNavigationRequest()&&r.frame()===page?.mainFrame()&&new URL(r.url()).origin!==origin)){keep(networkRows,{url:cleanUrl(r.url()),blocked:true,method:r.method()});return route.abort('blockedbyclient');}return route.continue();});
  page=await context.newPage();page.setDefaultTimeout(5000);
  page.on('pageerror',e=>keep(consoleRows,{type:'pageerror',text:e.message.slice(0,2000)}));
  page.on('console',m=>{if(['error','warning'].includes(m.type()))keep(consoleRows,{type:m.type(),text:m.text().slice(0,2000)});});
  page.on('response',r=>keep(networkRows,{url:cleanUrl(r.url()),status:r.status(),method:r.request().method()}));
  page.on('requestfailed',r=>keep(networkRows,{url:cleanUrl(r.url()),error:r.failure()?.errorText,method:r.method()}));
  page.on('dialog',async d=>{dialogCount++;keep(dialogs,{type:d.type(),message:d.message().slice(0,2000)});await d.dismiss();});page.on('popup',p=>p.close());
  await page.goto(v.url,{waitUntil:'domcontentloaded',timeout:20000});return snapshot();
 }
 if(v.action==='close'){if(browser)await browser.close();browser=null;return {closed:true};}
 if(!page||!browser?.isConnected())throw Error('Browser session is not active. Open the current project preview first; completed actions are not replayed.');
 const locator=()=>{if(typeof v.selector!=='string'||!v.selector.trim()||v.selector.length>500)throw Error('A CSS selector is required');return page.locator(v.selector);};
 if(v.action==='click')await locator().click();
 else if(v.action==='fill')await locator().fill(String(v.value??''));
 else if(v.action==='select')await locator().selectOption(String(v.value??''));
 else if(v.action==='press'){if(!['Enter','Tab','Escape','Backspace','ArrowDown','ArrowUp'].includes(v.value))throw Error('Unsupported key');await locator().press(v.value);}
 else if(v.action==='assert'){
  const target=v.target??'auto';
  if(!['auto','text','value','checked','visible','enabled','count','dialog_count'].includes(target))throw Error('Unsupported assertion target');
  let observed;
  if(target==='dialog_count')observed={actual:dialogCount,source:'dialog_count'};
  else if(target==='count')observed={actual:await locator().count(),source:'count'};
  else if(target==='visible')observed={actual:await locator().isVisible(),source:'visible'};
  else if(target==='enabled')observed={actual:await locator().isEnabled(),source:'enabled'};
  else observed=await locator().evaluate((el,target)=>{
   let source=target;
   if(source==='auto')source=el.tagName==='INPUT'&&['checkbox','radio'].includes(el.type)?'checked':['INPUT','TEXTAREA','SELECT'].includes(el.tagName)?'value':'text';
   if(source==='checked'&&!(el.tagName==='INPUT'&&['checkbox','radio'].includes(el.type)))throw Error('checked requires a checkbox or radio input');
   if(source==='value'&&!('value' in el))throw Error('Selected element has no value property');
   return {actual:source==='text'?(el.innerText||el.textContent||''):el[source],source};
  },target);
  const actual=String(observed.actual);
  const passed=v.contains?String(actual).includes(v.expected):String(actual).trim()===v.expected;
  const receipt={selector:v.selector,target,source:observed.source,expected:v.expected,actual:actual.slice(0,2000),passed};assertions.push(receipt);
  return {...await snapshot(),assertion:receipt};
 }else if(v.action==='screenshot'){
  const dir=path.resolve(v.directory);fs.mkdirSync(dir,{recursive:true});
  if(!/^[a-f0-9]{32}$/.test(v.id))throw Error('Invalid artifact identifier');
  await page.screenshot({path:path.join(dir,v.id+'.png'),fullPage:false});return {...await snapshot(),artifact_id:v.id};
 }else if(v.action==='viewport'){
  if(![375,768,1280].includes(v.width))throw Error('Unsupported viewport');await page.setViewportSize({width:v.width,height:800});
 }else if(v.action==='reload'){consoleRows=[];networkRows=[];assertions=[];await page.reload({waitUntil:'domcontentloaded',timeout:20000});}
 else if(v.action!=='snapshot')throw Error('Unknown browser action');
 return snapshot();
}
let chain=Promise.resolve();readline.createInterface({input:process.stdin}).on('line',line=>{chain=chain.then(async()=>{let result;try{result={ok:true,result:await execute(JSON.parse(line))};}catch(e){result={ok:false,error:String(e.message).slice(0,2000)};}process.stdout.write(JSON.stringify(result)+'\n');});});
process.stdin.on('end',async()=>{if(browser)await browser.close();process.exit(0);});
