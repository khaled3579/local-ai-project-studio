'use strict';
const fs=require('node:fs'),path=require('node:path'),readline=require('node:readline'),crypto=require('node:crypto');
const {chromium}=require('./runtime/assistant/node_modules/playwright');
let browser,context,page,origin,stateKey,lastInteraction=null,networkSequence=0,consoleRows=[],networkRows=[],assertions=[],dialogs=[],dialogCount=0;
let expectedPopup=false;
const keep=(array,value)=>{array.push(value);if(array.length>100)array.shift();};
const cleanUrl=value=>{try{const u=new URL(value);return u.origin+u.pathname;}catch{return String(value).slice(0,300);}};
function observePage(target){
 target.setDefaultTimeout(5000);
 target.on('pageerror',e=>keep(consoleRows,{type:'pageerror',text:e.message.slice(0,2000)}));
 target.on('console',m=>{if(['error','warning'].includes(m.type()))keep(consoleRows,{type:m.type(),text:m.text().slice(0,2000)});});
 target.on('dialog',async d=>{dialogCount++;keep(dialogs,{type:d.type(),message:d.message().slice(0,2000)});await d.dismiss();});
}
function allowedRequest(url){try{const u=new URL(url);return u.origin===origin;}catch{return false;}}
async function interactionState(){
 const state=await page.locator('body').evaluate(root=>({text:root.innerText.slice(0,20000),images:Array.from(root.querySelectorAll('img')).slice(0,128).map(el=>({src:el.currentSrc||el.src,width:el.naturalWidth,height:el.naturalHeight})),fields:Array.from(root.querySelectorAll('input,select,textarea')).slice(0,256).map(el=>({id:el.id,name:el.name,value:String(el.value).slice(0,10000),checked:!!el.checked,selected:el.selectedOptions?Array.from(el.selectedOptions).map(v=>v.index):undefined}))}));
 return crypto.createHmac('sha256',stateKey).update(JSON.stringify([state,dialogCount])).digest('hex');
}
async function scriptLoading(){
 return page.evaluate(()=>{const scripts=Array.from(document.scripts).filter(el=>{const type=(el.type||'').trim().toLowerCase();return !type||type==='module'||['text/javascript','application/javascript','text/ecmascript','application/ecmascript'].includes(type);});return {executable_script_elements:scripts.length,external_script_paths:scripts.filter(el=>el.src).slice(0,12).map(el=>{try{return new URL(el.src).pathname.slice(0,300)}catch{return ''}}),inline_script_elements:scripts.filter(el=>!el.src&&el.textContent.trim()).length,inline_event_attributes:Array.from(document.querySelectorAll('*')).some(el=>Array.from(el.attributes).some(a=>/^on[a-z]+$/i.test(a.name))),scope:'Declared document scripts only; not proof of successful execution or acceptance'};});
}
function missingScriptHint(loading){return loading.executable_script_elements===0&&!loading.inline_event_attributes?'No executable script element or inline event handler is declared by this page. Inspect this HTML entrypoint and the intended client script/load order; a script file on disk does not run unless loaded. If required, repair the real application wiring, validate, reopen, upload one input and assert actual output. This observation is not conversion or acceptance evidence.':null;}
async function selectorCatalog(){
 return page.evaluate(()=>{
  const nodes=Array.from(document.querySelectorAll('input:not([type="hidden"]),select,textarea,button,a[href],img,[role="button"]'));
  function unique(selector){try{return document.querySelectorAll(selector).length===1}catch{return false}}
  function selectorFor(el){
   if(el.id){const selector='#'+CSS.escape(el.id);if(unique(selector))return selector;}
   const name=el.getAttribute('name');if(name){const selector=el.tagName.toLowerCase()+'[name="'+CSS.escape(name)+'"]';if(unique(selector))return selector;}
   const parts=[];let node=el;
   while(node&&node.nodeType===1&&parts.length<16){const tag=node.tagName.toLowerCase();const siblings=node.parentElement?Array.from(node.parentElement.children).filter(v=>v.tagName===node.tagName):[node];parts.unshift(tag+':nth-of-type('+(siblings.indexOf(node)+1)+')');const selector=parts.join(' > ');if(selector.length>480)return null;if(unique(selector))return selector;node=node.parentElement;}
   return null;
  }
  return {total:nodes.length,entries:nodes.slice(0,96).map(el=>{
   const selector=selectorFor(el);if(!selector)return null;
   const tag=el.tagName.toLowerCase(),type=el.getAttribute('type')||'';
   const label=String(el.labels?.[0]?.textContent||el.getAttribute('aria-label')||(['button','a'].includes(tag)?el.textContent:'')||el.getAttribute('placeholder')||'').replace(/\s+/g,' ').trim().slice(0,100);
   return {selector,tag,type,label,suggested_action:type==='file'?'upload':tag==='select'?'select':tag==='textarea'||tag==='input'&&!['checkbox','radio','button','submit','reset'].includes(type)?'fill':tag==='img'?'assert':'click'};
  }).filter(Boolean),scope:'Actual current DOM selectors only; input values omitted. Choose the action and expected outcome from the preserved criterion. This catalog is not acceptance evidence.'};
 });
}
async function snapshot(){
 const fields=await page.locator('body').evaluate(root=>Array.from(root.querySelectorAll('input,select,textarea')).slice(0,256).map(el=>({id:el.id,name:el.name,type:el.type,value:String(el.value).slice(0,10000),checked:!!el.checked,selected:el.selectedOptions?Array.from(el.selectedOptions).map(v=>v.index):undefined})));
 const signature=crypto.createHmac('sha256',stateKey).update(JSON.stringify(fields)).digest('hex');
 return {selector_catalog:await selectorCatalog(),script_loading:await scriptLoading(),url:cleanUrl(page.url()),viewport:page.viewportSize(),title:await page.title(),snapshot:(await page.locator('body').ariaSnapshot()).slice(0,10000),text:(await page.locator('body').innerText()).slice(0,6000),state_signature:signature,console:consoleRows.slice(-25),network:networkRows.slice(-35),assertions:assertions.slice(-30),dialog_count:dialogCount,dialogs:dialogs.slice(-25),diagnostics:dialogCount>1?[{code:'multiple_dialogs',severity:'warning',count:dialogCount,message:'One interaction emitted multiple dialogs. Check for duplicate event handlers; assert dialog_count immediately after the action.'}]:[]};
}
async function execute(v){
 if(!['assert','snapshot','screenshot'].includes(v.action)){dialogs=[];dialogCount=0;}
 if(v.action==='open'){
  if(v.reuse_context){
   if(!page||!browser?.isConnected())throw Error('Owned preview session ended; prepare a fresh page explicitly. No actions were replayed.');
   if(new URL(v.url).origin!==origin)throw Error('Page switching must stay inside the same owned preview origin');
   consoleRows=[];networkRows=[];assertions=[];lastInteraction=null;networkSequence=0;
   await page.goto(v.url,{waitUntil:'domcontentloaded',timeout:20000});
   return {...await snapshot(),navigation_context_preserved:true,scope:'Explicit page navigation within the owned browser context only; no interaction or assertion replayed'};
  }
  if(browser)await browser.close();origin=new URL(v.url).origin;
  stateKey=/^[a-f0-9]{32}$/.test(v.state_key||'')?v.state_key:crypto.randomBytes(16).toString('hex');
  if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin))throw Error('Only the owned project preview is allowed');
  consoleRows=[];networkRows=[];assertions=[];lastInteraction=null;networkSequence=0;
  browser=await chromium.launch({headless:true,args:['--disable-gpu'],timeout:20000});context=await browser.newContext({viewport:{width:1280,height:800},acceptDownloads:false,serviceWorkers:'block'});
  await context.route('**/*',async route=>{const r=route.request();if(!allowedRequest(r.url())){keep(networkRows,{url:cleanUrl(r.url()),blocked:true,method:r.method()});return route.abort('blockedbyclient');}return route.continue();});
  page=null;expectedPopup=false;
  context.on('page',target=>{observePage(target);if(page&&!expectedPopup)target.close().catch(()=>{});});
  context.on('response',r=>keep(networkRows,{url:cleanUrl(r.url()),status:r.status(),method:r.request().method(),sequence:++networkSequence}));
  context.on('requestfailed',r=>keep(networkRows,{url:cleanUrl(r.url()),error:r.failure()?.errorText,method:r.method()}));
  page=await context.newPage();
  await page.goto(v.url,{waitUntil:'domcontentloaded',timeout:20000});return snapshot();
 }
 if(v.action==='close'){if(browser)await browser.close();browser=null;lastInteraction=null;return {closed:true};}
 if(!page||!browser?.isConnected())throw Error('Browser session is not active. Open the current project preview first; completed actions are not replayed.');
 const locator=()=>{if(typeof v.selector!=='string'||!v.selector.trim()||v.selector.length>500)throw Error('A CSS selector is required');return page.locator(v.selector);};
 let pendingInteraction=null;
 if(['click','fill','select','press','upload'].includes(v.action)){
  lastInteraction=null;
  pendingInteraction={action:v.action,selector:v.selector,before:await interactionState(),network_after:networkSequence};
 }
 if(v.action==='click'){
  if(await locator().evaluate(el=>el.tagName==='INPUT'&&el.type==='file'))throw Error('Clicking a file input does not upload a file. Use browser_project action=upload, selector, value=existing project image/CSV path, bounded data:image/png;base64 PNG INPUT, or data:text/csv;base64 UTF-8 CSV INPUT with actual application headers. File selection alone is not import, conversion or persistence evidence.');
  expectedPopup=await locator().evaluate(el=>!!el.closest('a[target="_blank"]'));
  const popup=expectedPopup?page.waitForEvent('popup',{timeout:5000}).catch(()=>null):null;
  try{
   await locator().click();
   if(popup){
    const opened=await popup;if(!opened)throw Error('Expected owned link did not open a page');
    try{await opened.waitForLoadState('domcontentloaded',{timeout:5000});if(!allowedRequest(opened.url()))throw Error('Popup left the owned project origin');}
    catch(error){await opened.close();throw error;}
    page=opened;
   }
  }finally{expectedPopup=false;}
 }
 else if(v.action==='upload'){
  if(!await locator().evaluate(el=>el.tagName==='INPUT'&&el.type==='file'))throw Error('upload requires a file INPUT selector');
  const input=v.upload;if(!input)throw Error('Host-approved upload input is required');
  await locator().setInputFiles(input.path||{name:input.name,mimeType:input.mime,buffer:Buffer.from(input.base64,'base64')});
 }
 else if(v.action==='fill')await locator().fill(String(v.value??''));
 else if(v.action==='select')await locator().selectOption(String(v.value??''));
 else if(v.action==='press'){if(!['Enter','Tab','Escape','Backspace','ArrowDown','ArrowUp'].includes(v.value))throw Error('Unsupported key');await locator().press(v.value);}
 else if(v.action==='assert'){
  const target=v.target??'auto';
  if(!['auto','text','value','checked','visible','enabled','count','dialog_count','image_format'].includes(target))throw Error('Unsupported assertion target');
  let observed;
  if(target==='dialog_count')observed={actual:dialogCount,source:'dialog_count'};
  else if(target==='image_format'){
   await locator().waitFor({state:'attached',timeout:5000});
   try{await page.waitForFunction(selector=>{const el=document.querySelector(selector);return el?.tagName==='IMG'&&el.complete&&el.naturalWidth>0;},v.selector,{timeout:5000});}catch(error){if(error.name!=='TimeoutError')throw error;}
   const img=await locator().evaluate(el=>({tag:el.tagName,src:el.currentSrc||el.src||'',decoded:el.complete&&el.naturalWidth>0&&el.naturalHeight>0}));
   if(img.tag!=='IMG')throw Error('image_format requires an IMG with actual conversion output; text labels are not format evidence');
   let bytes,sourceKind='data_url';
   if(img.src.length<=6*1024*1024&&/^data:image\/webp;base64,[A-Za-z0-9+/]+={0,2}$/.test(img.src)){
    bytes=Buffer.from(img.src.split(',')[1],'base64');
   }else if(allowedRequest(img.src)){
    // Server-side converters return owned HTTP resources rather than data URLs.
    // Fetch actual bounded bytes from THIS isolated origin only; reject redirects.
    const response=await fetch(img.src,{redirect:'error',signal:AbortSignal.timeout(5000)});
    if(!response.ok||response.headers.get('content-type')?.split(';')[0].trim()!=='image/webp')throw Error('Owned image response is not image/webp');
    const chunks=[];let length=0;
    for await(const chunk of response.body){length+=chunk.length;if(length>4*1024*1024)throw Error('Owned image exceeds the 4 MB evidence limit');chunks.push(chunk);}
    bytes=Buffer.concat(chunks);sourceKind='owned_http';
   }else{const hint=missingScriptHint(await scriptLoading());throw Error('image_format requires a bounded image/webp data URL or actual WebP output on the owned preview origin; never a remote URL or fixture'+(hint?' '+hint:''));}
   const valid=bytes.length>=20&&bytes.length<=4*1024*1024&&bytes.toString('ascii',0,4)==='RIFF'&&bytes.toString('ascii',8,12)==='WEBP'&&bytes.readUInt32LE(4)+8===bytes.length&&['VP8 ','VP8L','VP8X'].includes(bytes.toString('ascii',12,16))&&img.decoded;
   observed={actual:valid?'image/webp':'invalid',source:'image_format',image_format:{mime:valid?'image/webp':null,signature:valid?'RIFF/WEBP':null,decoded:!!img.decoded,bytes:bytes.length,source_kind:sourceKind}};
  }
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
  const interaction=lastInteraction?{action:lastInteraction.action,selector:lastInteraction.selector,state_changed:lastInteraction.before!==await interactionState(),mutation_observed:networkRows.some(row=>row.sequence>lastInteraction.network_after&&allowedRequest(row.url)&&['POST','PUT','PATCH','DELETE'].includes(row.method)&&row.status>=200&&row.status<300)}:null;
  const receipt={selector:v.selector,target,source:observed.source,expected:v.expected,actual:actual.slice(0,2000),passed,interaction,...(observed.image_format?{image_format:observed.image_format}:{})};assertions.push(receipt);
  return {...await snapshot(),assertion:receipt};
 }else if(v.action==='screenshot'){
  const dir=path.resolve(v.directory);fs.mkdirSync(dir,{recursive:true});
  if(!/^[a-f0-9]{32}$/.test(v.id))throw Error('Invalid artifact identifier');
  await page.screenshot({path:path.join(dir,v.id+'.png'),fullPage:false});return {...await snapshot(),artifact_id:v.id};
 }else if(v.action==='viewport'){
  lastInteraction=null;
  if(![375,768,1280].includes(v.width))throw Error('Unsupported viewport');await page.setViewportSize({width:v.width,height:800});
 }else if(v.action==='reload'){consoleRows=[];networkRows=[];assertions=[];lastInteraction=null;await page.reload({waitUntil:'domcontentloaded',timeout:20000});}
 else if(v.action!=='snapshot')throw Error('Unknown browser action');
 if(pendingInteraction)lastInteraction=pendingInteraction;
 const result=await snapshot();
 if(v.action==='upload'){
  result.upload_receipt={selector:v.selector,selected_files:await locator().evaluate(el=>Array.from(el.files||[]).slice(0,3).map(file=>({name:file.name.slice(0,160),mime:file.type,bytes:file.size}))),scope:'Input selection only; NOT conversion, persistence or acceptance evidence'};
  result.output_images=await page.locator('img').evaluateAll(images=>images.slice(0,8).map(image=>({id:image.id,decoded:image.complete&&image.naturalWidth>0&&image.naturalHeight>0,src_kind:image.currentSrc?.startsWith('data:image/webp;')?'webp_data_url':image.currentSrc?.startsWith('blob:')?'blob':image.currentSrc?'other':'empty'})));
  const csv=result.upload_receipt.selected_files.some(file=>file.mime==='text/csv'||file.name.toLowerCase().endsWith('.csv'));
  result.input_kind=csv?'csv':'image';
  result.next_action=csv?'CSV INPUT is selected. Inspect the actual import status/rows and assert the required content; verify export content and persistence separately. Selecting a file is not successful import, export or saved-data evidence. No image output is required for CSV. Do not repeat uploading the same input.':missingScriptHint(result.script_loading)||(result.output_images.length?'The input is already selected. Finish any scenario prerequisites, then assert the actual resulting output/state. Do not repeatedly select the same file. For WebP use image_format on actual converted output; selection/visibility is not format or saved-data proof.':'The input is selected but no IMG output is exposed on this page. Inspect the actual conversion script/selector and implement missing output observation if needed; validate changed source, then upload once and assert the actual converter output. Do not repeat upload or use a prebuilt WebP output fixture.');
 }
 return result;
}
let chain=Promise.resolve();readline.createInterface({input:process.stdin}).on('line',line=>{chain=chain.then(async()=>{let result;try{result={ok:true,result:await execute(JSON.parse(line))};}catch(e){result={ok:false,error:String(e.message).slice(0,2000)};}process.stdout.write(JSON.stringify(result)+'\n');});});
process.stdin.on('end',async()=>{if(browser)await browser.close();process.exit(0);});
