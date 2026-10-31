/* Recommendations are drafts; only the existing load action persists them. */
(()=>{
  let modelKey='',revision=0,edits=0,current=null,offline=false,timer;
  const panel=document.createElement('div');panel.id='autoProfilePanel';panel.className='panel';
  panel.innerHTML='<h2>إعداد تلقائي لكل نموذج</h2><p class="note">تُسترجع إعداداتك المحفوظة أولًا؛ النموذج الجديد يحصل على توصية حسب الأوزان والبطاقات. التقدير لا يثبت أسرع أداء.</p><label class="field"><input id="autoProfileEnabled" type="checkbox" checked> تجهيز الإعداد عند اختيار النموذج</label><button id="autoProfileDefaults">استعادة الإعدادات الافتراضية</button><button id="autoProfileSaved">استرجاع إعداداتي المحفوظة</button><p id="autoProfileStatus" class="note" role="status"></p><p class="note">يمكنك تعديل القيم أدناه. الحفظ والتطبيق يتمان عبر زر «تحميل وحفظ» بعد نجاح التحميل؛ الاستعادة هنا تجهّز المسودة فقط.</p>';
  $('enginePanel').insertBefore(panel,$('tabbyControls'));
  function target(){return engineDraft==='tabbyapi'?{engine:'tabbyapi',model:$('tabbyModel').value.trim()}:{engine:'llamacpp',model_id:selected()?.id}}
  function identity(){const t=target();return t.engine+'|'+(t.model||t.model_id||'')}
  function windowProfileRequest(payload){return request('/api/model-profile',payload)}
  function busy(){return offline||['loading','stopping'].includes(lastState?.status)||!!lastState?.generating}
  function buttons(){ $('autoProfileDefaults').disabled=busy();$('autoProfileSaved').disabled=busy()||!current?.has_saved }
  function fill(config){
    if(config.engine==='tabbyapi'){
      const values={tabbyContext:config.context,tabbyCache:config.cache_mode,tabbyDevices:config.devices,
        tabbyCpuOffload:config.cpu_offload_layers||0,tabbyExpertSplit:config.cpu_split_experts||0,
        tabbyGpuBudget:JSON.stringify(config.gpu_split||[]),tabbyDraftTokens:config.draft_tokens||2,tabbyChunk:config.chunk_size||2048};
      for(const [id,value] of Object.entries(values))$(id).value=value;
      $('tabbyParallel').checked=!!config.tensor_parallel;$('tabbyMtp').checked=!!config.mtp;$('tabbyHeretic').checked=!!config.heretic;
      $('tabbyMode').value=config.parameters?.mode||'defaults';$('tabbyGeneration').value=JSON.stringify(config.parameters?.generation||{},null,2);$('tabbyStop').value=JSON.stringify(config.parameters?.stop||[]);
      tabbyDraftInitialized=true;
    }else{
      $('context').value=config.context;$('mtp').checked=!!config.mtp;$('projector').value=config.projector||'';$('expertsUsed').value=config.experts_used??'';
      const params=config.parameters||{mode:'defaults'};$('parameterMode').value=params.mode;
      for(const [group,fields] of Object.entries(parameterSchema))for(const [key,spec] of Object.entries(fields)){
        const input=parameterInputs.get(parameterId(group,key));
        input.value=params.mode==='custom'&&params[group]?.[key]!==undefined?params[group][key]:selected()?.parameter_defaults?.values?.[group]?.[key]??spec.default;
        $(input.id+'-source').textContent=params.mode==='custom'&&params[group]?.[key]!==undefined?'إعداد النموذج المحدد':selected()?.parameter_defaults?.sources?.[group]?.[key]||'افتراضي المحرك';
      }
      $('parameterStop').value=JSON.stringify(params.stop||[]);setParameterBusy();
      loadPlanDraft=config.load_plan||null;if($('plannerReserve'))$('plannerReserve').value=config.load_plan?.reserve_gib??1.5;
      plannerModel=selected()?.id;plannerKey='';scheduleLoadPreview();
    }
    // Re-render control enabled states without resetting the newly selected draft.
    if(lastState)render(lastState);
  }
  async function prepare(mode='recommended'){
    const selectedKey=identity(),edit=edits,request=++revision,payload=target();
    if(!payload.model&&!payload.model_id)return;
    if(busy())return;
    $('autoProfileStatus').textContent='جارٍ قراءة إعدادات النموذج والبطاقات…';
    try{
      await parameterReady;
      const data=await windowProfileRequest(payload);
      if(request!==revision||selectedKey!==identity()||edit!==edits||busy()){if(request===revision&&selectedKey===identity())$('autoProfileStatus').textContent='بقيت تعديلاتك الحالية؛ لم تستبدلها الاستجابة المتأخرة.';return;}
      current=data;buttons();
      const config=mode==='saved'?data.saved:mode==='defaults'?data.defaults:data.recommended;
      if(!config)throw Error('لا يوجد تخصيص محفوظ لهذا النموذج');
      fill(config);
      const source=mode==='saved'||(mode==='recommended'&&data.has_saved)?'تخصيصك المحفوظ':data.basis==='measured'?'إعداد من قياس سابق متوافق':'إعداد تلقائي محسوب';
      $('autoProfileStatus').textContent=source+' · مسودة حتى التحميل والحفظ. '+data.warnings.join(' · ');
    }catch(error){if(request===revision&&selectedKey===identity())$('autoProfileStatus').textContent=error.message}
  }
  document.querySelector('aside').addEventListener('input',event=>{
    if(event.target.closest('#autoProfilePanel'))return;
    edits++;
    if(event.target.id==='tabbyModel'){clearTimeout(timer);timer=setTimeout(observe,600)}
  });
  document.querySelector('aside').addEventListener('change',event=>{
    if(event.target.closest('#autoProfilePanel'))return;
    edits++;
    if(['model','engineChoice','tabbyModel'].includes(event.target.id))observe();
  });
  function observe(){
    buttons();const next=identity();
    if(next===modelKey||busy()||!lastState||!parameterSchema||lastState.load_capabilities?.status!=='ready')return;
    modelKey=next;current=null;buttons();
    if($('autoProfileEnabled').checked)prepare();
  }
  $('autoProfileDefaults').onclick=()=>prepare('defaults');$('autoProfileSaved').onclick=()=>prepare('saved');
  $('autoProfileEnabled').onchange=()=>{revision++;if($('autoProfileEnabled').checked){modelKey='';observe()}};
  const previousRender=render;render=function(s){offline=false;previousRender(s);observe()};
  const previousDisconnected=disconnected;disconnected=function(){offline=true;revision++;previousDisconnected();buttons()};
  parameterReady.then(observe);observe();
})();
