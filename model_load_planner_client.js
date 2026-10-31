/* Read-only load planning. Drafts apply only through the existing Load & Save action. */
let loadPlanDraft=null,plannerModel=null,plannerInventory='',plannerKey='',plannerRevision=0,plannerTimer,plannerOffline=false;
function loadPlanConfig(){if($('plannerReserve')&&!$('plannerReserve').validity.valid)throw Error('راجع هامش الذاكرة التقديري');return loadPlanDraft||undefined}
function plannerRuntime(key){
  if($('parameterMode').value==='defaults')return selected()?.parameter_defaults?.values?.runtime?.[key]??parameterSchema.runtime[key]?.default;
  const input=parameterInputs.get(parameterId('runtime',key));
  return typeof parameterSchema.runtime[key]?.default==='number'?Number(input.value):input.value;
}
function plannerCustom(){
  if($('parameterMode').value==='defaults'){
    for(const [group,fields] of Object.entries(parameterSchema))for(const [key,spec] of Object.entries(fields))
      parameterInputs.get(parameterId(group,key)).value=selected()?.parameter_defaults?.values?.[group]?.[key]??spec.default;
  }
  $('parameterMode').value='custom';setParameterBusy();
}
function plannerSet(key,value){
  parameterInputs.get(parameterId('runtime',key)).value=value;
  $(parameterId('runtime',key)+'-source').textContent='مسودة خطة التحميل — غير محفوظة';
}
function plannerPayload(){return {model_id:$('model').value,context:Number($('context').value),mtp:$('mtp').checked,
  projector:$('projector').value,experts_used:$('expertsUsed').disabled||!$('expertsUsed').value.trim()?null:Number($('expertsUsed').value),
  parameters:parameterConfig(),load_plan:loadPlanConfig()}}
function scheduleLoadPreview(){clearTimeout(plannerTimer);plannerTimer=setTimeout(loadPreview,180)}
function syncPlannerDevices(){
  const inventory=lastState?.load_capabilities||{},devices=inventory.devices||[];
  const key=JSON.stringify(devices.map(d=>[d.id,d.name]));
  if(key!==plannerInventory){
    plannerInventory=key;$('plannerDevices').replaceChildren();
    for(const device of devices){
      const label=document.createElement('label');label.className='planner-device';
      const input=document.createElement('input');input.type='checkbox';input.value=device.id;input.dataset.plannerDevice='';
      const text=document.createElement('span');text.textContent=device.id+' · '+device.name;
      const small=document.createElement('small');small.className='note';small.dataset.deviceMemory=device.id;
      label.append(input,text,small);$('plannerDevices').append(label);
      input.onchange=()=>{
        const chosen=[...document.querySelectorAll('[data-planner-device]:checked')].map(box=>box.value);
        if(!chosen.length){input.checked=true;$('plannerDraft').textContent='اختر بطاقة واحدة على الأقل، أو الاختيار التلقائي.';return}
        plannerCustom();plannerSet('devices',chosen.join(','));plannerSet('main_gpu',0);
        plannerSet('split_mode',chosen.length===1?'none':'layer');plannerSet('tensor_split',chosen.map(()=>1).join(','));
        $('plannerAuto').checked=false;$('plannerProfile').value='custom';loadPlanDraft={profile:'custom',reserve_gib:Number($('plannerReserve').value)};
        $('plannerDraft').textContent='اختيار البطاقات مسودة؛ سيطبق عند «تشغيل وحفظ» فقط.';plannerKey='';scheduleLoadPreview();
      };
    }
  }
  const selectedDevices=String(plannerRuntime('devices')||'auto'),automatic=selectedDevices==='auto';
  $('plannerAuto').checked=automatic;
  const busy=plannerOffline||['loading','stopping'].includes(lastState?.status);
  for(const input of document.querySelectorAll('[data-planner-device]')){
    input.checked=automatic||selectedDevices.split(',').includes(input.value);input.disabled=busy||!inventory.features?.['--device'];
    const device=devices.find(row=>row.id===input.value),gpu=(lastState?.gpus||[]).find(row=>row.index===device?.physical_index);
    const memory=input.parentElement.querySelector('small');
    memory.textContent=gpu?.memory_total_mib!=null?'المستخدم '+(gpu.memory_mib/1024).toFixed(1)+' / '+(gpu.memory_total_mib/1024).toFixed(1)+' GiB'+(lastState.gpu_stale?' · قراءة قديمة':''):'قراءة الذاكرة الحالية غير متاحة';
  }
  for(const id of ['plannerAuto','plannerProfile','plannerPrepare','plannerReserve','plannerRefresh'])$(id).disabled=busy;
  $('plannerEngine').textContent=plannerOffline?'اتصال اللوحة غير متاح':inventory.status==='ready'?'المحرك الحالي: '+inventory.engine:inventory.error||'جارٍ اكتشاف خيارات المحرك وبطاقاته…';
}
function renderLoadPlanner(model=selected()){
  if(!parameterSchema||!$('loadPlanner'))return;
  if(!model){++plannerRevision;$('plannerSummary').textContent='اختر نموذجًا لتقدير التحميل.';$('plannerCards').replaceChildren();$('plannerCompatibility').textContent='';return}
  if(plannerModel!==model.id){
    plannerModel=model.id;
    const saved=[lastState?.active,lastState?.saved,model.saved_profile].find(row=>row?.model===model.path);
    loadPlanDraft=saved?.load_plan?{...saved.load_plan}:null;
    $('plannerProfile').value=loadPlanDraft?.profile||'custom';$('plannerReserve').value=loadPlanDraft?.reserve_gib??1.5;
    $('plannerDraft').textContent='الإعدادات المعروضة مسودة؛ لا تُغيّر النموذج المحمّل حتى التشغيل والحفظ.';
    plannerKey='';
  }
  syncPlannerDevices();scheduleLoadPreview();
}
async function loadPreview(){
  if(typeof engineDraft!=='undefined'&&engineDraft==='tabbyapi')return;
  if(plannerOffline||!selected()||!parameterSchema)return;
  let payload;
  try{payload=plannerPayload()}catch(error){$('plannerSummary').textContent='راجع مسودة المعاملات: '+error.message;return}
  const key=JSON.stringify([payload,lastState?.gpu_updated,lastState?.pid,lastState?.status,lastState?.load_capabilities?.status]);
  if(key===plannerKey)return;plannerKey=key;
  const revision=++plannerRevision,modelId=selected().id;
  try{
    const report=await request('/api/load-preview',payload);
    if(revision!==plannerRevision||selected()?.id!==modelId||JSON.stringify(plannerPayload())!==JSON.stringify(payload))return;
    const number=value=>value==null?'غير محسوم':value.toFixed(2)+' GiB';
    $('plannerSummary').textContent='الأوزان التقريبية: '+number(report.weights_gib)+' · KV التقريبي: '+number(report.kv_gib);
    $('plannerCards').replaceChildren();
    for(const card of report.cards){
      const section=document.createElement('div');section.className='planner-memory';
      const title=document.createElement('strong');title.textContent=card.id+' · '+card.name;
      const body=document.createElement('p');body.textContent='الأوزان '+number(card.weights_gib)+' · KV '+number(card.kv_gib)+' · بدل التشغيل '+number(card.workspace_allowance_gib)+' · الهامش '+number(card.reserve_gib);
      const total=document.createElement('p');total.textContent='الخطة مع تفاوت التقدير: '+number(card.planned_gib)+' / السعة '+number(card.total_gib)+' · الحر الآن '+number(card.free_gib);
      const fit=document.createElement('span');fit.className='badge';fit.textContent={within_total:'ضمن السعة الكلية تقديريًا',exceeds_total:'يتجاوز السعة الكلية تقديريًا',unknown:'الملاءمة غير محسومة'}[card.fit];
      section.append(title,body,total,fit);$('plannerCards').append(section);
    }
    const compat=report.compatibility;
    $('plannerCompatibility').textContent='ترويسة GGUF: '+(compat.gguf_header?'مقروءة':'غير مؤكدة')+' · البطاقات المختارة: '+(compat.selected_devices_present?'مكتشفة':'تحتاج مراجعة')+' · هذا الإعداد: '+(compat.load_confirmed?'محمّل حاليًا':'لم يُثبت تحميله')+' · Flash Attention: '+(compat.engine_flags['--flash-attn']?'خيار متاح في المحرك؛ دعم البنية يثبت بالتحميل':'غير معلن في المحرك');
    $('plannerWarnings').replaceChildren();
    for(const warning of report.warnings){const item=document.createElement('li');item.textContent=warning;$('plannerWarnings').append(item)}
    $('plannerMethod').textContent=report.method;
  }catch(error){if(revision===plannerRevision)$('plannerSummary').textContent='تعذر تقدير الخطة: '+error.message}
}
function preparePerformanceProfile(){
  const profile=$('plannerProfile').value,model=selected();if(!model)return;
  if(profile==='custom'){loadPlanDraft={profile:'custom',reserve_gib:Number($('plannerReserve').value)};$('plannerDraft').textContent='الإعداد المخصص يبقي اختياراتك الحالية؛ عدّلها من لوحة المعاملات.';scheduleLoadPreview();return}
  const changes=[],beforeContext=Number($('context').value),runtime=Object.fromEntries(Object.keys(parameterSchema.runtime).map(key=>[key,plannerRuntime(key)]));
  plannerCustom();
  const layers=model.metadata?.[model.architecture+'.block_count'];
  const values={gpu_layers:Number.isInteger(layers)?layers+1:999,flash_attention:'on',cache_type_k:'q8_0',cache_type_v:'q8_0',
    batch_size:profile==='fast'?1024:512,ubatch_size:256};
  for(const [key,value] of Object.entries(values)){
    if(runtime[key]!==value)changes.push(parameterSchema.runtime[key].label+': '+runtime[key]+' ← '+value);
    plannerSet(key,value);
  }
  if(profile==='fast')$('context').value=Math.min(beforeContext,32768,model.declared_context||beforeContext);
  if(profile==='long')$('context').value=Math.min(Math.max(beforeContext,65536),model.declared_context||Math.max(beforeContext,65536));
  if(Number($('context').value)!==beforeContext)changes.push('السياق: '+beforeContext+' ← '+$('context').value);
  loadPlanDraft={profile,reserve_gib:Number($('plannerReserve').value)};
  $('plannerDraft').textContent='ترشيح أولي غير مقاس؛ لم تتغير معاملات أخذ العينات أو MTP أو الخبراء أو الرؤية. '+(changes.length?changes.join(' · '):'القيم المقترحة مطابقة للمسودة الحالية.');
  plannerKey='';renderLoadPlanner();
}
async function initLoadPlanner(){
  await parameterReady;
  if(!parameterSchema)throw Error('إعدادات النموذج غير متاحة؛ أعد فتح الصفحة بعد عودة الاتصال.');
  const section=document.createElement('section');section.className='panel';section.id='loadPlanner';
  section.innerHTML='<div class="top"><h2>تخطيط التحميل والأداء</h2><span class="badge">مسودة قبل التحميل</span></div><p id="plannerEngine" class="note"></p><p class="note">التقدير لكل بطاقة منفصلة؛ إجمالي VRAM ليس ذاكرة موحدة. لا يُغيّر اختيار ملف أداء النموذج حتى «تشغيل وحفظ».</p><label class="check"><input id="plannerAuto" type="checkbox">اختيار البطاقات تلقائيًا كما يراها المحرك</label><div id="plannerDevices" class="planner-grid"></div><div class="planner-grid"><div><label class="field" for="plannerProfile">ملف الأداء</label><select id="plannerProfile"><option value="custom">الإعداد الحالي / مخصص</option><option value="balanced">متوازن — ترشيح أولي</option><option value="fast">استجابة أسرع — سياق أقصر</option><option value="long">سياق أطول — راجع الذاكرة</option></select></div><div><label class="field" for="plannerReserve">هامش تقديري لكل بطاقة — GiB</label><input id="plannerReserve" type="number" min="0" max="24" step="0.25" value="1.5"></div></div><div class="presets"><button id="plannerPrepare">تجهيز ملف الأداء للمراجعة</button><button id="plannerRefresh">تحديث التقدير</button></div><p id="plannerDraft" class="note"></p><p id="plannerSummary" role="status">اختر نموذجًا لتقدير التحميل.</p><div id="plannerCards" class="planner-grid"></div><p id="plannerCompatibility" class="note"></p><ul id="plannerWarnings" class="note warning"></ul><details><summary>منهج التقدير وحدوده</summary><p id="plannerMethod" class="note"></p><p class="note">الأرقام تقديرية وليست قياس سرعة أو ضمان نجاح التحميل. بيانات البنى الهجينة والرؤية وMTP قد تكون غير مكتملة. لم تتغير شروط تسليم المشاريع.</p></details>';
  $('error').after(section);
  $('plannerPrepare').onclick=preparePerformanceProfile;
  $('plannerRefresh').onclick=()=>{plannerKey='';scheduleLoadPreview()};
  $('plannerReserve').oninput=()=>{if(!$('plannerReserve').validity.valid)return;loadPlanDraft={profile:loadPlanDraft?.profile||'custom',reserve_gib:Number($('plannerReserve').value)};plannerKey='';scheduleLoadPreview()};
  $('plannerAuto').onchange=()=>{
    if(!$('plannerAuto').checked){
      const chosen=[...document.querySelectorAll('[data-planner-device]:checked')].map(box=>box.value);
      if(!chosen.length){syncPlannerDevices();return}
      plannerCustom();plannerSet('devices',chosen.join(','));plannerSet('main_gpu',0);
      plannerSet('split_mode',chosen.length===1?'none':'layer');plannerSet('tensor_split',chosen.map(()=>1).join(','));
      $('plannerProfile').value='custom';loadPlanDraft={profile:'custom',reserve_gib:Number($('plannerReserve').value)};
      $('plannerDraft').textContent='يمكنك الآن تخصيص البطاقات؛ الاختيار مسودة حتى التشغيل والحفظ.';
      plannerKey='';renderLoadPlanner();return;
    }
    plannerCustom();plannerSet('devices','auto');plannerSet('main_gpu',0);plannerSet('split_mode','layer');
    const count=lastState?.load_capabilities?.devices?.length||1;plannerSet('tensor_split',Array(count).fill(1).join(','));
    $('plannerProfile').value='custom';loadPlanDraft={profile:'custom',reserve_gib:Number($('plannerReserve').value)};plannerKey='';renderLoadPlanner();
  };
  document.querySelector('aside').addEventListener('input',event=>{if(event.target.id==='context'||event.target.id?.startsWith('parameter-runtime-')){$('plannerProfile').value='custom';loadPlanDraft={profile:'custom',reserve_gib:Number($('plannerReserve').value)}}scheduleLoadPreview()});
  document.querySelector('aside').addEventListener('change',scheduleLoadPreview);
  const previousRender=render;render=function(s){previousRender(s);plannerOffline=false;renderLoadPlanner()};
  const previousDisconnected=disconnected;disconnected=function(){previousDisconnected();plannerOffline=true;syncPlannerDevices();$('plannerSummary').textContent='التقدير غير مؤكد أثناء انقطاع الاتصال.'};
  renderLoadPlanner();
}
initLoadPlanner().catch(error=>showError('تعذر تجهيز تخطيط التحميل: '+error.message));
