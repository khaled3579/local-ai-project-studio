/* Optional backend selection. Drafts never change the running engine. */
let engineDraft='llamacpp',engineDraftTouched=false,tabbyDraftInitialized=false,engineConnected=true;
(()=>{
  const panel=document.createElement('section');panel.className='panel';panel.id='enginePanel';
  panel.innerHTML=`<h2>محرك التشغيل المحلي</h2>
    <label class="field" for="engineChoice">المحرك المراد تحميله</label>
    <select id="engineChoice"><option value="llamacpp">llama.cpp — ملفات GGUF</option><option value="tabbyapi">TabbyAPI / ExLlamaV3 — EXL3 / safetensors</option></select>
    <p class="note" id="engineInstalled"></p><p class="note">الاختيار مسودة. يبقى المحرك الحالي حتى نجاح «تحميل وحفظ». لا تعمل المحركات معًا ولا تُعاد الأدوات المنفذة عند التبديل.</p>
    <div id="tabbyControls" hidden>
      <label class="field" for="tabbyModel">مسار مجلد النموذج المحلي</label><input id="tabbyModel" dir="ltr" placeholder="G:\\models\\my-exl3-model" style="width:100%">
      <p class="note">يحتوي config.json وملفات safetensors والتوكنيزر. ملفات GGUF تبقى في محرك llama.cpp. دعم البنية يتأكد عند التحميل؛ وجود الملفات وحده لا يثبت التوافق.</p>
      <label class="field" for="tabbyContext">السياق — مضاعفات 256</label><input id="tabbyContext" type="number" min="512" max="2147483392" step="256" value="8192">
      <label class="field" for="tabbyCache">ذاكرة KV</label><select id="tabbyCache"><option>FP16</option><option>Q8</option><option>Q6</option><option>Q4</option></select>
      <label class="field" for="tabbyDevices">بطاقات CUDA — auto أو 0,1</label><input id="tabbyDevices" value="auto" dir="ltr">
      <label class="field"><input id="tabbyParallel" type="checkbox"> توازي Tensor عبر البطاقات — التوافق يثبت عند التحميل</label>
      <label class="field" for="tabbyCpuOffload">طبقات خبراء MoE في RAM — صفر لتعطيل النقل</label><input id="tabbyCpuOffload" type="number" min="0" step="1" value="0">
      <label class="field" for="tabbyExpertSplit">خبراء CPU داخل كل طبقة — صفر لتعطيل التقسيم</label><input id="tabbyExpertSplit" type="number" min="0" step="1" value="0">
      <p class="note">اختر نقل طبقات كاملة أو تقسيم الخبراء داخل الطبقات. الباقي على GPU، مع ترتيب متكيف للخبراء؛ لا تُغيّر الأوزان الأصلية.</p>
      <label class="field" for="tabbyGpuBudget">ميزانية GPU لكل بطاقة — GiB بصيغة JSON</label><input id="tabbyGpuBudget" dir="ltr" value="[]"><p class="note">مثال [21,21] للبطاقتين. [] يترك التوزيع تلقائيًا. هذه ميزانيات ذاكرة وليست نسبًا.</p>
      <label class="field"><input id="tabbyMtp" type="checkbox"> تفعيل رأس MTP المحلي</label>
      <label class="field" for="tabbyDraftTokens">عدد التوكنات المقترحة في الجولة</label><input id="tabbyDraftTokens" type="number" min="1" step="1" value="2">
      <label class="field" for="tabbyChunk">دفعة معالجة الإدخال — مضاعفات 256</label><input id="tabbyChunk" type="number" min="256" step="256" value="2048">
      <p class="note">نقل الخبراء إلى RAM يستخدم توزيع الطبقات مع تعطيل Tensor Parallel. قد يخفض السرعة؛ لا يغيّر الأوزان.</p>
      <label class="field"><input id="tabbyHeretic" type="checkbox"> تفعيل ملحق Heretic المراجع لهذا النموذج</label><p class="note" id="hereticState"></p>
      <label class="field" for="tabbyMode">معاملات التوليد</label><select id="tabbyMode"><option value="defaults">افتراضيات النموذج / TabbyAPI</option><option value="custom">تخصيص مستقل</option></select>
      <label class="field" for="tabbyGeneration">التخصيص بصيغة JSON</label><textarea id="tabbyGeneration" rows="5" dir="ltr" style="width:100%">{}</textarea>
      <p class="note">مثال توضيحي فقط: {"temperature":0.2,"top_p":0.95,"repeat_penalty":1}. القيم غير المحددة تبقى للمحرك أو generation_config.json. لا تُنقل افتراضيات llama.cpp تلقائيًا.</p>
      <label class="field" for="tabbyStop">تسلسلات التوقف — JSON</label><textarea id="tabbyStop" rows="2" dir="ltr" style="width:100%">[]</textarea>
      <p class="note warning">تقدير ذاكرة GGUF لا يُطبّق على EXL3. توضّح لوحة الإعداد التلقائي مصدر التوصية؛ لا يُعد تقدير السعة قياسًا للسرعة.</p>
      <p class="note" id="tabbyPrepare">تهيئة البيئة المعزولة: Prepare-Tabby.ps1. لا يحمّل المثبّت نموذجًا ولا يشغّل اختبارات.</p>
      <div class="actions"><button id="tabbyApply" class="primary">تحميل وحفظ TabbyAPI</button><button id="tabbyStopModel">إيقاف النموذج الحالي</button></div>
    </div><p class="note" id="engineCurrent"></p><p class="note warning" id="tabbyWarnings"></p>`;
  document.querySelector('aside').prepend(panel);
  const info=document.createElement('section');info.className='panel';info.id='tabbyInfo';info.hidden=true;
  info.innerHTML='<h2>محرك TabbyAPI الاختياري</h2><p class="note" id="tabbySummary"></p><p class="note">تستمر المحادثة والمهام في مساحة المشاريع الحالية. تبقى شروط التسليم والتحقق المحلي من معاملات الأدوات وسجلات التنفيذ ونقطة الاستئناف مستقلة عن المحرك.</p><a href="http://127.0.0.1:18078/">فتح محادثة مساحة المشاريع ↗</a>';
  document.querySelector('main').prepend(info);

  function renderEngine(s){
    const active=s.active?.engine||'llamacpp',entry=s.engines?.find(e=>e.id==='tabbyapi');
    if(!engineDraftTouched){engineDraft=s.engine||'llamacpp';$('engineChoice').value=engineDraft}
    const tabby=engineDraft==='tabbyapi',busy=['loading','stopping'].includes(s.status),generating=!!s.generating;
    $('engineChoice').disabled=busy; $('tabbyControls').hidden=!tabby;
    // Existing controls stay intact; only presentation follows the draft choice.
    for(const section of document.querySelectorAll('aside > section'))if(section!==panel)section.hidden=tabby;
    $('modelName').closest('section').hidden=tabby;
    if($('loadPlanner'))$('loadPlanner').hidden=tabby;
    info.hidden=!tabby;
    $('engineInstalled').textContent=entry?.installed?(s.active&&active==='tabbyapi'&&s.status==='ready'?'تم تحميل التكوين الحالي؛ جودة المهام والأداء لم يُقيّما':'TabbyAPI مثبت في بيئة معزولة؛ التشغيل والتوافق لم يُختبرا'):'TabbyAPI غير مهيأ؛ المحرك الحالي متاح. شغّل Prepare-Tabby.ps1 لتهيئته.';
    $('tabbyWarnings').textContent=s.active&&active==='tabbyapi'?(s.parameters?.warnings||[]).join(' · '):'';
    $('tabbyPrepare').hidden=!!entry?.installed;
    $('engineCurrent').textContent='المحرك المحمّل: '+(s.active?(active==='tabbyapi'?'TabbyAPI / ExLlamaV3':'llama.cpp'):'لا يوجد نموذج محمّل');
    $('tabbySummary').textContent=s.active&&active==='tabbyapi'?'النموذج: '+s.active.model+' · السياق الفعلي: '+fmt(s.actual_context)+' · طبقات كاملة في RAM: '+(s.active.cpu_offload_layers||0)+' · خبراء CPU لكل طبقة: '+(s.tabby_runtime?.cpu_experts_per_layer||0)+' · MTP: '+(s.tabby_runtime?.mtp_loaded?'محمّل':'معطل')+(s.heretic?.applied?' · Heretic مفعّل على '+s.heretic.wrapped_sublayers+' طبقة فرعية':''):'اختر مجلد نموذج متوافق. لا يُحوّل البرنامج GGUF إلى EXL3 ولا ينزّل أوزانًا تلقائيًا.';
    $('hereticState').textContent=s.heretic?.applied?'أكد المحرك تطبيق الملحق. جودة التوليد لم تُختبر.':'ملحق اختياري؛ يُراجع توافقه ويُطبّق أثناء التحميل، دون تشغيل كود من مجلد النموذج.';
    if(!tabbyDraftInitialized&&s.tabby_saved){
      const c=s.tabby_saved;$('tabbyModel').value=c.model||'';$('tabbyContext').value=c.context||8192;
      $('tabbyCache').value=c.cache_mode||'FP16';$('tabbyDevices').value=c.devices||'auto';$('tabbyParallel').checked=!!c.tensor_parallel;$('tabbyCpuOffload').value=c.cpu_offload_layers||0;
      $('tabbyHeretic').checked=!!c.heretic;
      $('tabbyExpertSplit').value=c.cpu_split_experts||0;$('tabbyGpuBudget').value=JSON.stringify(c.gpu_split||[]);$('tabbyMtp').checked=!!c.mtp;$('tabbyDraftTokens').value=c.draft_tokens||2;
      $('tabbyChunk').value=c.chunk_size||2048;
      $('tabbyMode').value=c.parameters?.mode||'defaults';$('tabbyGeneration').value=JSON.stringify(c.parameters?.generation||{},null,2);$('tabbyStop').value=JSON.stringify(c.parameters?.stop||[]);
      tabbyDraftInitialized=true;
    }
    for(const id of ['tabbyModel','tabbyContext','tabbyCache','tabbyDevices','tabbyParallel','tabbyCpuOffload','tabbyExpertSplit','tabbyGpuBudget','tabbyMtp','tabbyDraftTokens','tabbyChunk','tabbyHeretic','tabbyMode'])$(id).disabled=busy||!engineConnected;
    const custom=$('tabbyMode').value==='custom';$('tabbyGeneration').disabled=busy||!custom||!engineConnected;$('tabbyStop').disabled=$('tabbyGeneration').disabled;
    $('tabbyApply').disabled=busy||generating||!entry?.installed||!engineConnected;
    $('tabbyApply').textContent=generating?'انتظر اكتمال الرد':'تحميل وحفظ TabbyAPI';
    $('tabbyStopModel').disabled=busy||generating||!s.pid||!engineConnected;
    const link=$('chat').closest('section').querySelector('a');
    link.href=active==='tabbyapi'&&s.active?'http://127.0.0.1:18078/':'http://127.0.0.1:18080/';
    link.textContent=active==='tabbyapi'&&s.active?'فتح محادثة مساحة المشاريع ↗':'فتح المحادثة مستقلة ↗';
    if(active==='tabbyapi'&&s.active){$('chat').hidden=true;$('empty').hidden=false;$('empty').textContent='TabbyAPI متصل بمساعد المشاريع. افتح محادثة مساحة المشاريع لمتابعة محادثاتك ومهامك المحفوظة.'}
    if(s.engine_error)showError(s.engine_error);
  }
  const previousRender=render;render=function(s){previousRender(s);engineConnected=true;renderEngine(s)};
  const previousDisconnected=disconnected;disconnected=function(){previousDisconnected();engineConnected=false;$('engineChoice').disabled=true;if(lastState)renderEngine(lastState)};
  $('engineChoice').onchange=()=>{engineDraftTouched=true;engineDraft=$('engineChoice').value;if(lastState)renderEngine(lastState)};
  $('tabbyMode').onchange=()=>{if(lastState)renderEngine(lastState)};
  $('tabbyExpertSplit').oninput=()=>{if(Number($('tabbyExpertSplit').value)>0){$('tabbyCpuOffload').value=0;$('tabbyParallel').checked=false}};
  $('tabbyCpuOffload').oninput=()=>{if(Number($('tabbyCpuOffload').value)>0)$('tabbyExpertSplit').value=0};
  $('tabbyApply').onclick=()=>{
    showError('');try{
      if(!['tabbyContext','tabbyCpuOffload','tabbyExpertSplit','tabbyDraftTokens','tabbyChunk'].every(id=>$(id).reportValidity()))return;
      const custom=$('tabbyMode').value==='custom';
      action('/api/apply',{engine:'tabbyapi',model:$('tabbyModel').value.trim(),context:Number($('tabbyContext').value),
        cache_mode:$('tabbyCache').value,devices:$('tabbyDevices').value.trim(),tensor_parallel:$('tabbyParallel').checked,cpu_offload_layers:Number($('tabbyCpuOffload').value),heretic:$('tabbyHeretic').checked,
        cpu_split_experts:Number($('tabbyExpertSplit').value),gpu_split:JSON.parse($('tabbyGpuBudget').value),mtp:$('tabbyMtp').checked,draft_tokens:Number($('tabbyDraftTokens').value),
        chunk_size:Number($('tabbyChunk').value),
        parameters:{mode:$('tabbyMode').value,generation:custom?JSON.parse($('tabbyGeneration').value):{},stop:custom?JSON.parse($('tabbyStop').value):[]}});
    }catch(error){showError('راجع إعدادات TabbyAPI: '+error.message)}
  };
  $('tabbyStopModel').onclick=()=>action('/api/stop');
  if(lastState)renderEngine(lastState);
})();
