/* Local, dependency-free workbench. File operations are scoped and hash checked. */
(() => {
  const mode = document.createElement('div');mode.className='view-mode';mode.setAttribute('role','group');mode.setAttribute('aria-label','مستوى عرض الأدوات');
  mode.innerHTML='<button id="simpleView" aria-pressed="true">مبسط</button><button id="advancedView" aria-pressed="false">متقدم</button>';
  document.querySelector('.header-actions').prepend(mode);
  const navigation=document.createElement('nav');navigation.className='wb-switcher';navigation.setAttribute('aria-label','مساحات العمل');
  navigation.innerHTML='<button data-view="chat" aria-pressed="true">المحادثة</button><button data-view="editor" aria-pressed="false">محرر الملفات</button><button data-view="preview" aria-pressed="false">معاينة المشروع</button><button data-view="quality" aria-pressed="false">الفحص والجودة</button><button id="nextEditorDraft" hidden>مسودات الملفات</button><button id="workbenchCancel" class="danger" hidden>إيقاف المهمة</button><button id="workbenchFocus" aria-pressed="false">توسيع المساحة</button><small id="workbenchModeHint">الأدوات الأساسية للعمل اليومي</small>';
  document.querySelector('.context-strip').after(navigation);
  const bench=document.createElement('section');bench.id='studioWorkbench';bench.hidden=true;bench.setAttribute('aria-label','أدوات تطوير المشروع');
  bench.innerHTML=`
  <section class="wb-panel" data-panel="editor" hidden>
   <div class="wb-toolbar"><strong id="editorPath">اختر ملفًا من مستكشف المشروع</strong><button id="editorCompare">مقارنة التغييرات</button><button id="editorReload">قراءة النسخة الحالية</button><button class="primary" id="editorSave">حفظ الملف</button></div>
   <div class="wb-search"><input id="editorFind" aria-label="بحث داخل الملف" placeholder="ابحث داخل الملف…"/><button id="editorFindNext">التالي</button><span>Ctrl+S حفظ · Ctrl+F بحث · Tab مسافة بادئة</span></div>
   <div class="wb-status" id="editorStatus" role="status">الحفظ ينشئ نسخة احتياطية ويلزم إعادة الفحص.</div>
   <div class="wb-code"><pre class="wb-gutter" id="editorLines" aria-hidden="true">1</pre><textarea id="codeEditor" aria-label="محتوى ملف المشروع" wrap="off" spellcheck="false" autocomplete="off" autocapitalize="off" disabled></textarea></div>
   <div class="wb-footer"><span id="editorPosition">سطر 1 · عمود 1</span><span id="editorDirty">لا يوجد ملف مفتوح</span></div>
  </section>
  <section class="wb-panel" data-panel="diff" hidden>
   <div class="wb-toolbar"><strong>مقارنة مع محتوى المحرر</strong><select id="editorVersion" aria-label="مصدر المقارنة"><option value="">الملف الحالي على القرص ← المحرر</option></select><button id="refreshDiff">تحديث المقارنة</button><button id="rebaseEditor" hidden>اعتماد نسخة القرص أساسًا للمسودة</button><button id="backToEditor">العودة للتحرير</button></div>
   <div class="wb-status" id="diffStatus" role="status"></div><pre class="wb-diff" id="editorDiff" aria-label="فروق الملف"></pre>
  </section>
  <section class="wb-panel" data-panel="preview" hidden>
   <div class="wb-toolbar"><strong>معاينة المشروع</strong><select id="previewEntry" aria-label="صفحة المعاينة"></select><button class="primary" id="startPreview">تشغيل المعاينة</button><button id="stopPreview">إيقاف التشغيل</button><button id="reloadPreview">تحديث</button><select id="previewSize" aria-label="حجم المعاينة"><option value="desktop">سطح المكتب</option><option value="tablet">جهاز لوحي · 768</option><option value="phone">هاتف · 375</option></select><a id="previewExternal" target="_blank" rel="noopener" hidden>فتح منفصل ↗</a></div>
   <div class="wb-status" id="previewStatus" role="status"></div><div class="wb-preview-hint" id="previewHint">اختر صفحة HTML. يبدأ التشغيل بعد اجتياز فحص حالي للمشروع. لتطبيقات Python ذات الواجهة الرسومية استخدم «تشغيل التطبيق»؛ تظهر نافذتها على الجهاز.</div>
   <div class="wb-preview-area" id="previewArea" data-size="desktop"><iframe id="projectFrame" title="معاينة التطبيق" sandbox="allow-scripts allow-forms allow-same-origin" referrerpolicy="no-referrer" hidden></iframe></div>
  </section>
  <section class="wb-panel" data-panel="quality" hidden>
   <div class="wb-toolbar"><strong>بوابة فحص المشروع</strong><button class="primary" id="qualityRun">فحص الآن</button><button id="qualityRefresh">تحديث النتائج</button><select id="qualityFilter" aria-label="تصفية نتائج الفحص"><option value="all">جميع الطبقات</option><option value="failed">الفاشلة فقط</option><option value="passed">الناجحة فقط</option></select><a class="wb-download" id="downloadQuality" download="project-validation.json" hidden>تنزيل التقرير</a></div>
   <div class="wb-quality-summary"><div class="wb-metric"><small>الحالة الحالية</small><strong id="qualityState">لم يُفحص</strong></div><div class="wb-metric"><small>طبقات ناجحة</small><strong id="qualityLayers">—</strong></div><div class="wb-metric"><small>اختبارات منفّذة فعليًا</small><strong id="qualityTests">—</strong></div><div class="wb-metric"><small>طبقات تحتاج متابعة</small><strong id="qualityFailures">—</strong></div></div>
   <p class="wb-quality-note" id="qualityNote"></p><div class="wb-checks" id="qualityChecks"></div>
  </section>`;
  ($('conversationLayout')||$('chat')).before(bench);
  let view='chat', file=null, fileProject=null, serial=0, saving=false, opening=false, draftTimer, qualityKey='', qualityBusy=false, qualityData=null, qualityURL=null, fileListKey='', previewURL=null, comparedDiskHash=null;
  let localStorageRef;try{localStorageRef=localStorage;}catch(_){localStorageRef={getItem(){return null},setItem(){throw Error('storage unavailable')},removeItem(){}};}
  const drafts=new EditorDrafts(localStorageRef);
  const dirtyPaths=new Set();let draftsProject=null;
  const scope=()=>({project:state?.project,session_id:state?.session_id});
  const sameScope=s=>state?.project===s.project&&state?.session_id===s.session_id;
  const busy=()=>state?.job?.status==='running'||state?.repair_run?.status==='running'||state?.importing;
  const dirty=()=>!!file&&$('codeEditor').value!==file.base;
  const post=(action,extra={},s=scope())=>api('/api/workbench',{...s,action,...extra});
  function status(id,text,error=false){$(id).textContent=text;$(id).classList.toggle('error',error);}
  function persistMode(value){
    document.body.dataset.viewMode=value;
    $('simpleView').setAttribute('aria-pressed',String(value==='simple'));$('advancedView').setAttribute('aria-pressed',String(value==='advanced'));
    $('workbenchModeHint').textContent=value==='simple'?'الأدوات الأساسية للعمل اليومي':'المحرر والتشخيص والتحكم الكامل';
    try{localStorageRef.setItem('studio.view.mode.v1',value);}catch(_){}
  }
  let initial='simple';try{if(localStorageRef.getItem('studio.view.mode.v1')==='advanced')initial='advanced';}catch(_){}
  persistMode(initial);$('simpleView').onclick=()=>persistMode('simple');$('advancedView').onclick=()=>persistMode('advanced');
  for(const id of ['autoRollback','autoRetry','maxAttempts','contextPercent','reviewContextSummary'])$(id).closest('label')?.classList.add('advanced-only');
  // Required recovery review and stop/resume stay visible in both modes.
  function heading(){
    const titles={chat:'محادثة المشروع ',editor:'محرر المشروع ',diff:'مقارنة التغييرات ',preview:'معاينة المشروع ',quality:'الفحص والجودة '};
    document.querySelector('.workspace-heading h2').firstChild.textContent=titles[view];
  }
  $('workbenchCancel').onclick=async()=>{try{await api('/api/cancel');await poll();}catch(error){status('editorStatus',error.message,true);}};
  $('workbenchFocus').onclick=()=>{const on=document.body.classList.toggle('workbench-focus');$('workbenchFocus').setAttribute('aria-pressed',String(on));$('workbenchFocus').textContent=on?'استعادة الحجم':'توسيع المساحة';};
  function setView(next){
    view=next;bench.hidden=next==='chat';document.body.classList.toggle('workbench-open',next!=='chat');
    for(const panel of bench.querySelectorAll('[data-panel]'))panel.hidden=panel.dataset.panel!==next;
    for(const button of navigation.querySelectorAll('[data-view]'))button.setAttribute('aria-pressed',String(button.dataset.view===next||(next==='diff'&&button.dataset.view==='editor')));
    heading();if(next==='chat'){document.body.classList.remove('workbench-focus');$('workbenchFocus').setAttribute('aria-pressed','false');$('workbenchFocus').textContent='توسيع المساحة';}
    if(next==='quality')loadQuality();
    if(next==='preview')paintPreview();else if($('projectFrame').hasAttribute('src')){$('projectFrame').removeAttribute('src');previewURL=null;}
  }
  for(const button of navigation.querySelectorAll('[data-view]'))button.onclick=()=>setView(button.dataset.view);
  function position(){
    const input=$('codeEditor'),head=input.value.slice(0,input.selectionStart).split('\n');
    $('editorPosition').textContent='سطر '+head.length+' · عمود '+(head.at(-1).length+1);
    $('editorDirty').textContent=file?(dirty()?'تغييرات غير محفوظة في المشروع · مسودة محلية':'مطابق للنسخة المفتوحة'):'لا يوجد ملف مفتوح';
  }
  let totalLines=1;
  function gutter(){const input=$('codeEditor'),first=Math.floor(input.scrollTop/21),visible=Math.min(150,Math.ceil(input.clientHeight/21)+2);$('editorLines').textContent=Array.from({length:Math.max(0,Math.min(visible,totalLines-first))},(_,i)=>first+i+1).join('\n');$('editorLines').style.paddingTop=(14-input.scrollTop%21)+'px';}
  function lines(){totalLines=$('codeEditor').value.split('\n').length;gutter();position();}
  function cache(){
    if(!file)return;
    if(dirty())dirtyPaths.add(file.path);else dirtyPaths.delete(file.path);
    drafts.stage(fileProject,file.path,file.sha256,file.base,$('codeEditor').value);
    clearTimeout(draftTimer);draftTimer=setTimeout(()=>{if(!drafts.flush())status('editorStatus','تعذر حفظ مسودة التحرير محليًا؛ احفظ الملف قبل إغلاق الصفحة.',true);},250);
  }
  async function openFile(path){
    const requested=++serial,s=scope();opening=true;updateControls();status('editorStatus','جارٍ فتح '+path+'…');
    try{
      if(file){cache();drafts.flush();}
      const value=await post('open',{path},s);if(requested!==serial||!sameScope(s))return;
      const recovered=drafts.read(s.project,value.path);
      file={path:value.path,sha256:recovered?.sha256||value.sha256,base:(recovered?.base??value.content).replace(/\r\n/g,'\n')};fileProject=s.project;
      $('codeEditor').value=recovered?.text??value.content;$('codeEditor').setSelectionRange(0,0);$('codeEditor').scrollTop=0;$('codeEditor').scrollLeft=0;$('editorPath').textContent=value.path;
      $('codeEditor').disabled=false;lines();setView('editor');
      status('editorStatus',recovered?(recovered.sha256!==value.sha256?'استُعيدت المسودة، لكن الملف على القرص تغير. قارن النسختين قبل الحفظ؛ لن يُستبدل الملف الأحدث تلقائيًا.':'استُعيدت مسودة التحرير المحلية غير المحفوظة.'):'الحفظ ينشئ نسخة احتياطية. أعد الفحص قبل التشغيل أو التصدير.',!!recovered&&recovered.sha256!==value.sha256);
      if(window.matchMedia('(max-width:800px)').matches&&typeof closeSidebar==='function'){closeSidebar();if(typeof syncSidebarAccessibility==='function')syncSidebarAccessibility();}
      if(dirty())dirtyPaths.add(file.path);else dirtyPaths.delete(file.path);
      $('codeEditor').focus();gutter();updateControls();
    }catch(error){if(requested===serial&&sameScope(s)){status('editorStatus',error.message,true);setView('editor');}}
    finally{if(requested===serial){opening=false;updateControls();}}
  }
  showFile=openFile;
  $('codeEditor').oninput=()=>{cache();lines();status('editorStatus','تغييرات في المحرر؛ لم تُعدّل ملفات المشروع بعد.');status('diffStatus','تغير محتوى المحرر؛ حدّث المقارنة.');updateControls();};
  $('codeEditor').onscroll=gutter;window.addEventListener('resize',gutter);
  for(const event of ['click','keyup','select'])$('codeEditor').addEventListener(event,position);
  $('codeEditor').addEventListener('keydown',event=>{
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();save();}
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='f'){event.preventDefault();$('editorFind').focus();}
    if(event.key==='Tab'&&!event.shiftKey&&!$('codeEditor').readOnly){event.preventDefault();const input=$('codeEditor');input.setRangeText('    ',input.selectionStart,input.selectionEnd,'end');input.dispatchEvent(new Event('input'));}
  });
  function find(){
    const input=$('codeEditor'),query=$('editorFind').value;if(!query)return;
    let index=input.value.toLowerCase().indexOf(query.toLowerCase(),input.selectionEnd);
    if(index<0)index=input.value.toLowerCase().indexOf(query.toLowerCase());
    if(index<0){status('editorStatus','لا توجد نتيجة مطابقة.');return;}
    input.focus();input.setSelectionRange(index,index+query.length);input.scrollTop=Math.max(0,input.value.slice(0,index).split('\n').length-4)*21;position();
  }
  $('editorFindNext').onclick=find;$('editorFind').onkeydown=e=>{if(e.key==='Enter')find();};
  async function save(){
    if(!file||!dirty()||saving||opening||busy()||state.running_pid)return;
    const active=file,s=scope(),text=$('codeEditor').value;saving=true;updateControls();
    try{
      const value=await post('save',{path:active.path,sha256:active.sha256,content:text},s);
      if(!sameScope(s)||file!==active)return;
      // Typing during the request remains a separate unsaved draft.
      file.sha256=value.sha256;file.base=value.content;
      if($('codeEditor').value===text)$('codeEditor').value=value.content;
      drafts.committed(s.project,file.path,value.sha256,value.content);cache();drafts.flush();lines();
      status('editorStatus','حُفظ الملف مع نسخة احتياطية؛ يلزم فحص جديد قبل التشغيل والتصدير.');await poll();
    }catch(error){if(sameScope(s))status('editorStatus',error.message+' مسودتك باقية؛ قارن النسخة الحالية قبل أي تعديل.',true);}
    finally{saving=false;updateControls();}
  }
  $('editorSave').onclick=save;
  $('nextEditorDraft').onclick=()=>{if(dirtyPaths.size)openFile(dirtyPaths.values().next().value);};
  $('editorReload').onclick=async()=>{
    if(!file)return;
    if(dirty()){
      cache();drafts.flush();status('editorStatus','لديك مسودة غير محفوظة. احفظها أو افتح المقارنة؛ لن تُحذف بإعادة القراءة.',true);return;
    }
    await openFile(file.path);
  };
  async function comparison(loadVersions=false){
    if(!file)return;const active=file,s=scope();setView('diff');status('diffStatus','جارٍ حساب الفروق…');
    try{
      if(loadVersions){
        const versions=await post('versions',{path:active.path},s);if(file!==active||!sameScope(s))return;
        $('editorVersion').replaceChildren(new Option('الملف الحالي على القرص ← المحرر',''),...versions.versions.map(v=>new Option(new Date(v.timestamp*1000).toLocaleString('ar')+' · '+v.reason,v.id)));
      }
      const comparedText=$('codeEditor').value;
      const result=await post('compare',{path:active.path,sha256:active.sha256,content:comparedText,version_id:$('editorVersion').value||null},s);
      if(file!==active||!sameScope(s))return;
      if($('codeEditor').value!==comparedText){status('diffStatus','تغير النص أثناء المقارنة؛ حدّث المقارنة.');return;}
      comparedDiskHash=result.disk_sha256;$('rebaseEditor').hidden=!result.disk_changed||!!$('editorVersion').value;
      $('editorDiff').replaceChildren();
      for(const row of result.diff||[]){const line=document.createElement('span');line.textContent=row;line.className=row.startsWith('@@')?'diff-hunk':row.startsWith('+')?'diff-add':row.startsWith('-')?'diff-remove':'';$('editorDiff').append(line);}
      status('diffStatus',(result.disk_changed?'الملف على القرص تغيّر منذ فتحه؛ الحفظ بالبصمة القديمة مرفوض. ':'')+(result.summary_only?result.message:!result.changed?'لا توجد فروق.':`+${result.added} سطر مضاف · −${result.removed} سطر محذوف`)+(result.final_newline_changed?' · تغيرت نهاية السطر الأخير':'')+(result.truncated?' · العرض جزئي':'')+' · الاتجاه: '+result.source+' ← المحرر',result.disk_changed);
    }catch(error){status('diffStatus',error.message,true);}
  }
  $('rebaseEditor').onclick=async()=>{
    if(!file||busy())return;const active=file,s=scope();
    try{
      const current=await post('open',{path:active.path},s);if(file!==active||!sameScope(s))return;
      if(current.sha256!==comparedDiskHash)throw Error('تغير الملف بعد المقارنة؛ حدّث الفروق أولًا');
      file.base=current.content;file.sha256=current.sha256;cache();drafts.flush();
      $('rebaseEditor').hidden=true;status('diffStatus','حُدّث أساس المسودة بعد مراجعتك. لم يُحفظ الملف بعد؛ ارجع للتحرير ثم احفظ عند اعتماد النتيجة.');
    }catch(error){status('diffStatus',error.message,true);}
  };
  $('editorCompare').onclick=()=>comparison(true);$('refreshDiff').onclick=()=>comparison();$('editorVersion').onchange=()=>comparison();$('backToEditor').onclick=()=>setView('editor');
  const safePreview=url=>{try{const parsed=new URL(url);return parsed.protocol==='http:'&&parsed.hostname==='127.0.0.1'&&/^\d+$/.test(parsed.port)&&parsed.port!==location.port;}catch(_){return false;}};
  function paintPreview(){
    const url=state?.preview_url,valid=!!url&&!!state.running_pid&&safePreview(url);
    $('projectFrame').hidden=!valid;$('previewExternal').hidden=!valid;$('previewHint').hidden=valid;
    if(valid){$('previewExternal').href=url;status('previewStatus','معاينة التشغيل الحالية · '+url);if(view==='preview'&&previewURL!==url){$('projectFrame').src=url;previewURL=url;}}
    else{if($('projectFrame').hasAttribute('src'))$('projectFrame').removeAttribute('src');previewURL=null;status('previewStatus',state?.running_pid?'التطبيق يعمل في نافذته؛ لا توجد معاينة ويب.':'المعاينة غير مشغّلة.');}
  }
  $('startPreview').onclick=async()=>{try{await post('preview',{path:$('previewEntry').value});await poll();paintPreview();}catch(error){status('previewStatus',error.message,true);}};
  $('stopPreview').onclick=async()=>{try{await api('/api/stop-app');await poll();paintPreview();}catch(error){status('previewStatus',error.message,true);}};
  $('reloadPreview').onclick=()=>{previewURL=null;paintPreview();};$('previewSize').onchange=()=>{$('previewArea').dataset.size=$('previewSize').value;};
  const labels={web_javascript_syntax:'صياغة JavaScript',web_local_assets:'صفحات الويب ومواردها',validation_incomplete:'الفحص قيد التنفيذ',python_syntax:'صياغة Python',python_unit_tests:'اختبارات Python',javascript_syntax:'صياغة JavaScript',html_assets:'ملفات HTML ومواردها',node_tests:'اختبارات Node',npm_test:'اختبارات npm',npm_build:'بناء المشروع'};
  function paintQuality(){
    const data=qualityData;if(!data)return;
    const names={running:'جارٍ العمل',unchecked:'لم يُفحص',outdated:'قواعد قديمة',stale:'يلزم فحص جديد',passed:'اجتاز الفحص',failed:'يحتاج إصلاحًا'};
    $('qualityState').textContent=names[data.status]||data.status;$('qualityState').className=data.status==='passed'?'ok':data.status==='failed'?'bad':'';
    $('qualityLayers').textContent=data.passed+' / '+data.checks.length;$('qualityTests').textContent=data.executed;$('qualityFailures').textContent=data.failed;
    $('qualityNote').textContent=(data.timestamp?'آخر تقرير: '+new Date(data.timestamp*1000).toLocaleString('ar')+' · ':'')+(data.status==='passed'?'الفحص الحالي يتيح التشغيل والتصدير.':data.status==='running'?'العمل جارٍ؛ النتائج المعروضة آخر تقرير محفوظ وليست اعتمادًا للمهمة الحالية.':'يلزم اجتياز فحص حالي قبل التشغيل والتصدير.')+' الفحص يغطي الصياغة والبناء والاختبارات؛ التحقق البصري مستقل.';
    $('qualityChecks').replaceChildren();
    const filter=$('qualityFilter').value;
    const checks=data.checks.filter(c=>filter==='all'||(filter==='passed'?c.passed===true:c.passed!==true));
    if(!checks.length){const empty=document.createElement('p');empty.className='wb-empty';empty.textContent=data.checks.length?'لا توجد نتائج تطابق التصفية.':'لا توجد نتائج فحص محفوظة لهذا المشروع.';$('qualityChecks').append(empty);}
    for(const check of checks){
      const detail=document.createElement('details');detail.className='wb-check';const summary=document.createElement('summary');
      const name=document.createElement('strong');name.textContent=labels[check.name]||check.name;
      const badge=document.createElement('span');badge.className=check.passed===true?'badge ok':'badge bad';badge.textContent=check.passed===true?'نجاح':'فشل';
      const info=document.createElement('span');info.textContent=check.executed?check.executed+' اختبارًا منفّذًا':check.files?check.files+' ملفات':'طبقة تحقق';summary.append(name,badge,info);detail.append(summary);
      const output=document.createElement('pre');output.textContent=[check.stdout,check.stderr,check.error,check.errors?JSON.stringify(check.errors,null,2):''].filter(Boolean).join('\n')||'لا توجد مخرجات إضافية.';detail.append(output);
      if(check.passed!==true)detail.open=true;
      if(Array.isArray(check.errors))for(const error of check.errors){
        const path=error.path||error.file;if(!path||!state.files.some(f=>f.path===path))continue;
        const button=document.createElement('button');button.textContent='فتح '+path;button.onclick=()=>openFile(path);detail.append(button);
      }
      $('qualityChecks').append(detail);
    }
  }
  async function loadQuality(){
    if(qualityBusy||!state?.project)return;const s=scope();qualityBusy=true;
    try{const data=await post('validation',{},s);if(!sameScope(s))return;qualityData=data;paintQuality();if(qualityURL)URL.revokeObjectURL(qualityURL);qualityURL=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));$('downloadQuality').href=qualityURL;$('downloadQuality').hidden=false;}
    catch(error){if(sameScope(s))$('qualityNote').textContent=error.message;}finally{qualityBusy=false;if(!sameScope(s)&&view==='quality'&&state?.project)loadQuality();}
  }
  $('qualityFilter').onchange=paintQuality;$('qualityRefresh').onclick=loadQuality;
  $('qualityRun').onclick=async()=>{try{if(dirtyPaths.size)throw Error('احفظ مسودات الملفات أولًا؛ الفحص يعمل على ملفات القرص.');await api('/api/check');await poll();loadQuality();}catch(error){$('qualityNote').textContent=error.message;}};
  $('report').onclick=()=>{$('toolsDialog').close();setView('quality');};
  function updateControls(){
    const locked=busy()||saving||opening||!file||fileProject!==state?.project;
    $('workbenchCancel').hidden=!(state?.job?.status==='running'||state?.repair_run?.status==='running');
    $('editorSave').disabled=locked||!!state?.running_pid||!dirty();$('editorCompare').disabled=!file||busy();$('editorReload').disabled=!file||saving;
    $('codeEditor').readOnly=busy()||saving||opening;
    $('nextEditorDraft').hidden=!dirtyPaths.size;$('nextEditorDraft').textContent='مسودات الملفات ('+dirtyPaths.size+')';
    if(dirtyPaths.size){$('check').disabled=true;$('run').disabled=true;$('exportZip').disabled=true;}
    $('qualityRun').disabled=busy()||!state?.project||saving||!!dirtyPaths.size;$('qualityRun').title=state?.repair_settings?.autonomous_repair?'الفحص مع الإصلاح الآلي حسب إعدادات المشروع':'فحص الملفات والاختبارات دون تشغيل الإصلاح الآلي';
    const valid=!!state?.validation?.success&&!!state?.validation?.current;
    $('startPreview').disabled=busy()||!!dirtyPaths.size||!valid||!$('previewEntry').value||!!state?.running_pid;
    $('stopPreview').disabled=!state?.running_pid;$('reloadPreview').disabled=!state?.preview_url;
  }
  let lastProject=null;
  const previousRender=render;
  render=function(s){
    if(lastProject!==null&&lastProject!==s.project){cache();drafts.flush();serial++;opening=false;file=null;fileProject=null;qualityData=null;qualityKey='';for(const id of ['qualityState','qualityLayers','qualityTests','qualityFailures'])$(id).textContent='—';$('qualityNote').textContent='جارٍ تحميل تقرير المشروع الحالي…';$('codeEditor').value='';$('codeEditor').disabled=true;$('editorPath').textContent='اختر ملفًا من المشروع الحالي';$('editorDiff').replaceChildren();$('qualityChecks').replaceChildren();$('downloadQuality').hidden=true;qualityBusy=false;qualityURL=null;comparedDiskHash=null;fileListKey='';previewURL=null;$('projectFrame').removeAttribute('src');setView('chat');}
    if(draftsProject!==s.project){draftsProject=s.project;dirtyPaths.clear();for(const f of s.files){const d=drafts.read(s.project,f.path);if(d&&d.text!==d.base.replace(/\r\n/g,'\n'))dirtyPaths.add(f.path);}}
    lastProject=s.project;previousRender(s);heading();updateControls();
    const key=JSON.stringify([s.project,s.files.filter(f=>/\.html?$/i.test(f.path)).map(f=>f.path)]);
    if(key!==fileListKey){fileListKey=key;const previous=$('previewEntry').value;$('previewEntry').replaceChildren(...s.files.filter(f=>/\.html?$/i.test(f.path)).map(f=>new Option(f.path,f.path)));if([...$('previewEntry').options].some(o=>o.value===previous))$('previewEntry').value=previous;updateControls();}
    if(view==='preview')paintPreview();
    const qkey=JSON.stringify([s.project,s.session_id,s.validation?.timestamp,s.validation?.current,s.validation?.in_progress,s.job?.status,s.repair_run?.status]);
    if(qkey!==qualityKey){qualityKey=qkey;if(view==='quality')loadQuality();}
  };
  window.addEventListener('pagehide',()=>{cache();drafts.flush();});
  window.addEventListener('beforeunload',e=>{if(dirty()&&!drafts.flush()){e.preventDefault();e.returnValue='';}});
  // Avoid reading files or generating diffs until the user opens them.
  if(state)render(state);
})();
