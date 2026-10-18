/* Immediate local recovery plus acknowledged, atomic project storage. */
class ComposerDrafts {
 constructor({input,status,resolveButton,request,storage,delay=400}) {
  Object.assign(this,{input,status,resolveButton,request,storage,delay});
  this.scope=null;this.record=null;this.server=null;this.flight=null;this.timer=null;this.conflict=null;this.retryAt=0;this.localOK=true;this.attachments=0;this.uploading=false;
  input.addEventListener('input',()=>this.edit());
  resolveButton.onclick=()=>{if(this.conflict&&this.record){this.record.revision=this.conflict.revision;this.conflict=null;this.cache();this.flush().catch(()=>{})}};
 }
 key(scope=this.scope){return 'local-ai-composer:v1:'+encodeURIComponent(scope.project)+':'+scope.session_id}
 same(scope){return !!this.scope&&scope.project===this.scope.project&&scope.session_id===this.scope.session_id}
 read(scope){try{return JSON.parse(this.storage.getItem(this.key(scope))||'null')}catch{return null}}
 cache(){if(!this.scope||!this.record)return;try{this.storage.setItem(this.key(),JSON.stringify(this.record));this.localOK=true}catch{this.localOK=false}}
 fromServer(draft){return {text:draft.text||'',edit_id:draft.edit_id,revision:draft.revision||0,dirty:false,updated:draft.updated||0}}
 consumed(record,draft){return record?.edit_id&&(record.edit_id===draft.consumed_edit_id||(draft.consumed_ids||[]).includes(record.edit_id))}
 sync(s,uploading=false){
  this.uploading=uploading;this.attachments=(s.draft_attachments||[]).length;this.input.disabled=!s.project;
  if(!s.project||!s.session_id){this.paint();return}
  const scope={project:s.project,session_id:s.session_id};const draft=s.composer_draft||{text:'',revision:0};
  if(!this.same(scope)){
   const continuation=this.scope&&scope.project===this.scope.project&&(s.sessions||[]).some(v=>v.id===scope.session_id&&v.parent_id===this.scope.session_id);
   const carry=continuation&&this.record?.text?{...this.record}:null;
   this.cache();clearTimeout(this.timer);this.scope=scope;this.server=draft;this.conflict=null;this.retryAt=0;
   const local=this.read(scope);
   this.record=carry&&(carry.dirty||!draft.text)?{...carry,edit_id:crypto.randomUUID(),revision:draft.revision||0,dirty:true}:local?.dirty&&!this.consumed(local,draft)?local:this.fromServer(draft);
   if(this.input.value!==this.record.text)this.input.value=this.record.text;this.cache();
  }else{
   this.server=draft;
   if(this.consumed(this.record,draft)||(!this.record.dirty&&draft.revision>this.record.revision)){
    this.record=this.fromServer(draft);if(this.input.value!==this.record.text)this.input.value=this.record.text;this.cache();
   }
  }
  if(this.record?.dirty&&!this.flight&&!this.conflict&&Date.now()>=this.retryAt)this.schedule();
  this.paint();
 }
 edit(){
  if(!this.scope)return;
  this.record={text:this.input.value,edit_id:crypto.randomUUID(),revision:this.record?.revision||0,dirty:true,updated:Date.now()};
  this.cache();this.paint();this.schedule(this.localOK?this.delay:0);
 }
 capture(){if(!this.record?.edit_id||this.record.text!==this.input.value)this.edit();return {...this.record,scope:{...this.scope}}}
 schedule(delay=this.delay){clearTimeout(this.timer);this.timer=setTimeout(()=>this.flush().catch(()=>{}),delay)}
 async flush(){
  clearTimeout(this.timer);
  if(this.flight){await this.flight;if(this.record?.dirty&&!this.conflict)return this.flush();return !this.conflict}
  if(!this.scope||!this.record?.dirty)return true;
  if(this.conflict){this.paint();return false}
  const scope={...this.scope},snapshot={...this.record};
  this.flight=(async()=>{
   try{
    const result=await this.request('/api/draft',{...scope,text:snapshot.text,edit_id:snapshot.edit_id,revision:snapshot.revision});
    if(!this.same(scope))return true;
    if(result.conflict){this.conflict=result.draft;this.paint();return false}
    this.server=result.draft;
    if(this.record.edit_id===snapshot.edit_id){this.record=this.fromServer(result.draft);this.input.value=this.record.text}
    else this.record.revision=result.draft.revision;
    this.cache();this.retryAt=0;return true;
   }catch(error){this.retryAt=Date.now()+2500;this.saveError=error.message;return false}
   finally{this.flight=null;this.paint()}
  })();
  const saved=await this.flight;
  if(saved&&this.record?.dirty&&!this.conflict)return this.flush();
  return saved;
 }
 acknowledge(snapshot,draft){
  if(this.same(snapshot.scope)&&this.record?.edit_id===snapshot.edit_id){
   this.record=this.fromServer(draft||{text:'',revision:this.record.revision+1});if(this.input.value!==this.record.text)this.input.value=this.record.text;this.cache();this.paint();
  }
 }
 resize(){if(!this.input.style)return;this.input.style.height='auto';this.input.style.height=Math.min(180,Math.max(64,this.input.scrollHeight))+'px'}
 paint(){
  this.resize();
  this.resolveButton.hidden=!this.conflict;
  let message='حفظ تلقائي للمسودة';
  if(this.uploading)message='جارٍ رفع المرفقات… أبقِ الصفحة مفتوحة حتى الاكتمال';
  else if(this.conflict)message='مسودة أحدث في صفحة أخرى؛ احتُفظ بالنسختين. يمكنك اعتماد نسختك هنا.';
  else if(this.record?.dirty&&!this.localOK)message='تعذّر الحفظ في المتصفح؛ أبقِ الصفحة مفتوحة حتى يكتمل الحفظ في المشروع';
  else if(this.record?.dirty)message=this.retryAt>Date.now()?'المسودة محفوظة في المتصفح؛ سيُعاد حفظها في المشروع عند الاتصال':'محفوظة في المتصفح؛ جارٍ الحفظ في المشروع…';
  else if(this.record?.text||this.attachments)message=this.attachments?'المسودة والمرفقات محفوظة في المشروع':'المسودة محفوظة في المشروع';
  if(this.status.textContent!==message)this.status.textContent=message;this.status.dataset.saved=String(!this.record?.dirty&&!this.uploading);
 }
}
if(typeof module==='object'&&module.exports)module.exports={ComposerDrafts};
