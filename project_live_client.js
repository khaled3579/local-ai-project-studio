/* Live snapshots are presentation only. Reconnect never submits a task. */
(() => {
  let latest = null, connected = false, stopped = false, controller;
  let memoryScope, memoryRevision, memoryOffset;
  const memoryButton = document.createElement('button');
  memoryButton.id = 'projectRequirements';
  memoryButton.textContent = 'ذاكرة المتطلبات';
  memoryButton.title = 'عرض متطلبات المشروع وقراراته وتعديلها';
  $('contextSummary').insertAdjacentElement('afterend', memoryButton);
  const liveBadge = document.createElement('span');
  liveBadge.className = 'badge'; liveBadge.id = 'liveConnection';
  $('shellContextMeter').insertAdjacentElement('afterend', liveBadge);
  const usageBadge = document.createElement('span');
  usageBadge.id = 'tokenUsage'; usageBadge.className = 'badge';
  usageBadge.setAttribute('aria-live', 'polite');
  liveBadge.insertAdjacentElement('afterend', usageBadge);
  const acceptanceButton = document.createElement('button');
  acceptanceButton.id = 'acceptanceEvidence';
  acceptanceButton.textContent = 'أدلة القبول';
  usageBadge.insertAdjacentElement('afterend', acceptanceButton);
  let acceptanceEvidence;
  let operationKey='';
  function paintOperations(s) {
    const feed=$('operationFeed'),history=$('operationHistory');if(!feed||!history)return;
    const operations=(s.messages||[]).filter(isOperationMessage);
    // Compact adjacent identical display rows without changing execution/history.
    const groups=[];
    for(const [index,message] of operations.entries()){
      const {id,generation_id,...visible}=message;
      const signature=JSON.stringify(visible),previous=groups[groups.length-1];
      if(previous?.signature===signature)previous.items.push({message,index});
      else groups.push({signature,items:[{message,index}]});
    }
    const events=(s.events||[]).filter(event=>!event.session_id||event.session_id===s.session_id);
    $('answerCount').textContent=(s.messages||[]).filter(m=>!isOperationMessage(m)).length+' رسالة';
    $('operationCount').textContent=groups.length+' مجموعة · '+operations.length+' متابعة';
    const key=JSON.stringify([s.project,s.session_id,operations,events]);if(key===operationKey)return;
    operationKey=key;
    const follow=feed.scrollHeight-feed.scrollTop-feed.clientHeight<100,position=feed.scrollTop;
    const opened=new Set([...history.querySelectorAll('details[open]')].map(row=>row.dataset.key));
    history.replaceChildren();
    for(const [groupIndex,group] of groups.entries()){
      const {message,index}=group.items[0];
      const row=document.createElement('details');row.className='operation-item '+(message.kind==='reasoning'?'reasoning':'');row.dataset.key=message.id||'message-'+index;
      row.open=opened.has(row.dataset.key)||(groupIndex===groups.length-1&&!opened.size);
      const title=document.createElement('summary');title.textContent=message.kind==='continuation'?'متابعة تلقائية · نقطة الاستئناف':message.kind==='reasoning'?'تفكير النموذج':message.kind==='partial'?'توليد غير مكتمل':message.kind==='recovered'?'متابعة من السجل':'خطوة معالجة · '+(index+1);
      const body=document.createElement('div');body.className='operation-body';body.textContent=message.content||'';
      if(group.items.length>1)title.textContent+=' · تكررت '+group.items.length+' مرات';
      // Tool-only progress can contain the same tool name several times.
      const names=message.tool_names||[];
      if(names.length&&message.content==='الأدوات: '+names.join('، ')){
        const counts=new Map();for(const name of names)counts.set(name,(counts.get(name)||0)+1);
        body.textContent='الأدوات: '+[...counts].map(([name,count])=>name+(count>1?' × '+count:'')).join('، ');
      }
      row.append(title,body);history.append(row);
      if(group.items.length>1){
        const originals=document.createElement('details'),caption=document.createElement('summary');
        caption.textContent='عرض المتابعات الأصلية ('+group.items.length+')';originals.append(caption);
        for(const item of group.items){
          const original=document.createElement('div');original.className='operation-body';
          original.textContent='المتابعة '+(item.index+1)+'\n'+(item.message.content||'');
          originals.append(original);
        }
        row.append(originals);
      }
    }
    if(events.length){
      const group=document.createElement('details');group.className='operation-item';group.dataset.key='execution-events';group.open=opened.has(group.dataset.key)||!operations.length;
      const title=document.createElement('summary');title.textContent='سجل التنفيذ · '+events.length+' حدثًا';group.append(title);
      for(const event of events){
        const item=document.createElement('div');item.className='operation-body';
        const label={tool:'استخدام أداة',file_written:'حفظ ملف',command:'تنفيذ أمر',validation:'نتيجة فحص',error:'خطأ',tool_error:'خطأ أداة',chain_paused:'حفظ نقطة استئناف',model:'معالجة النموذج',delivered:'تسليم النتيجة',version_saved:'حفظ إصدار',stream_retry:'إعادة الاتصال'}[event.kind]||event.kind;
        const details=event.details||{};
        item.textContent=label+'\n'+(details.path||details.name||details.message||'');
        if(details.argv)item.textContent+='\n'+details.argv.join(' ');
        if(details.exit_code!==undefined)item.textContent+='\nرمز الخروج: '+details.exit_code;
        if(details.error)item.textContent+='\n'+String(details.error);
        if(event.kind==='validation')item.textContent+='\n'+(details.success?'ناجح':'يحتاج متابعة');
        group.append(item);
      }
      history.append(group);
    }
    if(!operations.length&&!events.length){const empty=document.createElement('div');empty.className='operation-empty';empty.textContent='ستظهر هنا خطوات المعالجة والأدوات، والتفكير إذا أرسله النموذج.';history.append(empty)}
    feed.scrollTop=follow?feed.scrollHeight:position;
  }
  acceptanceButton.onclick = () => {
    if (!acceptanceEvidence) return;
    const dialog = document.createElement('dialog');
    dialog.style.cssText = 'max-width:760px;width:90vw;max-height:85vh;overflow:auto;direction:rtl';
    const title = document.createElement('h2'); title.textContent = 'ربط المتطلبات باختبارات القبول'; title.tabIndex = -1;
    title.style.margin = '0';
    const close = document.createElement('button'); close.textContent = 'إغلاق'; close.onclick = () => dialog.close();
    close.style.flexShrink = '0';
    const header = document.createElement('div');
    header.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:12px;position:sticky;top:0;background:inherit;z-index:1;padding:8px 0';
    header.append(title, close);
    const note = document.createElement('p');
    note.textContent = acceptanceEvidence.validated
      ? 'اجتازت المتطلبات المربوطة الاختبارات وقت إعداد التقرير. تبقى ملاءمة الاختبارات وشمولها مسؤولية المراجعة.'
      : 'التسليم غير معتمد بعد؛ توجد أدلة أو روابط ناقصة، أو تحتاج الملفات إلى فحص حديث.';
    dialog.append(header, note);
    for (const row of acceptanceEvidence.requirements_coverage.items || []) {
      const section = document.createElement('section');
      const heading = document.createElement('h3'); heading.textContent = row.id + (row.requirement_ids?.length ? ' · ' + row.requirement_ids.join(', ') : '') + ' · ' + (row.passed ? 'دليل قبول مثبت' : 'دليل ناقص');
      const quote = document.createElement('p'); quote.textContent = row.quote; quote.dir = 'auto';
      const criterion = document.createElement('p'); criterion.textContent = row.criterion; criterion.dir = 'auto';
      const tests = document.createElement('pre'); tests.style.cssText = 'white-space:pre-wrap;overflow-wrap:anywhere;direction:ltr';
      tests.textContent = row.tests.join('\n');
      section.append(heading, quote, criterion, tests); dialog.append(section);
    }
    dialog.onclose = () => dialog.remove(); document.body.append(dialog); dialog.showModal(); title.focus({preventScroll:true}); dialog.scrollTop = 0;
  };

  function paintUsage(job) {
    const usage = job?.token_usage;
    usageBadge.hidden = !usage;
    if (!usage) { usageBadge.textContent = ''; return; }
    const fmt = value => Number(value || 0).toLocaleString('en-US');
    usageBadge.textContent = usage.measured_requests
      ? 'توكن المهمة · إدخال ' + fmt(usage.prompt_tokens) + ' · إخراج ' + fmt(usage.completion_tokens)
      : 'توكن المهمة · القياس غير متاح من المحرك';
    usageBadge.title = 'طلبات مقاسة ' + fmt(usage.measured_requests) + ' / ' + fmt(usage.requests)
      + ' · ردود غير مكتملة ' + fmt(usage.incomplete_requests)
      + ' · المجاميع تشمل الطلبات التي أرسل المحرك أرقامها فقط، بما فيها المحاولات المعادة. لا تمثل حجم السياق الحالي.';
  }

  function paint(packet) {
    if (!state) return;
    paintOperations(state);
    memoryButton.disabled = !state.project;
    $('saveRequirements').disabled = state.job?.status === 'running' || state.repair_run?.status === 'running' || (memoryScope && (memoryScope.project !== state.project || memoryScope.session_id !== state.session_id));
    liveBadge.textContent = connected ? 'بث مباشر' : 'تحديث دوري · إعادة اتصال البث';
    const same = packet && packet.project === state.project && packet.session_id === state.session_id;
    paintUsage(same && packet.job?.id === state.job?.id ? packet.job : state.job);
    const job = same && packet.job?.id === state.job?.id ? packet.job : state.job;
    if($('operationPhase'))$('operationPhase').textContent=job?.status==='running'?(job.phase||'المعالجة جارية'):({paused:'المهمة متوقفة مؤقتًا؛ التقدم محفوظ',complete:'اكتملت المعالجة',cancelled:'أُوقفت المهمة',failed:'توقفت المعالجة بسبب خطأ'}[job?.status]||'لا توجد عملية نشطة');
    acceptanceEvidence = typeof job?.delivery_evidence?.requirements_coverage === 'object' ? job.delivery_evidence : null;
    acceptanceButton.disabled = !acceptanceEvidence;
    acceptanceButton.textContent = acceptanceEvidence
      ? 'أدلة القبول · ' + acceptanceEvidence.requirements_coverage.items.filter(row => row.passed).length + '/' + acceptanceEvidence.requirements_coverage.items.length
      : 'أدلة القبول';
    const old = $('liveGeneration');
    if (!same) { old?.remove(); return; }
    if (!packet.generation || packet.job?.status !== 'running') {
      // Keep the last visible stream until the durable snapshot has caught up.
      // A completed stream is not itself a persisted conversation message.
      const saved = old && state.messages?.some(message => message.generation_id === old.dataset.generationId || (message.kind==='reasoning'&&message.generation_id?.startsWith(old.dataset.generationId+':attempt:')));
      if (saved || old?.dataset.hasOutput !== 'true') old?.remove();
      else if (old) {
        old.children[0].textContent = 'المساعد';
        old.children[2].textContent = 'آخر رد ظاهر · جارٍ تحديث سجل المحادثة';
      }
      return;
    }
    if (state.messages?.some(message => message.generation_id === packet.generation.id && message.kind !== 'partial')) {
      old?.remove(); return;
    }
    const streamFeed=$('operationFeed')||$('chat');
    const nearBottom = streamFeed.scrollHeight - streamFeed.scrollTop - streamFeed.clientHeight < 140;
    let box = old;
    if (!box) {
      box = document.createElement('div'); box.id = 'liveGeneration'; box.className = 'message assistant';
      const title = document.createElement('strong'); title.textContent = 'المعالجة المباشرة';
      const body = document.createElement('div'); body.style.whiteSpace = 'pre-wrap';
      const note = document.createElement('small'); note.className = 'hint';
      const reasoning=document.createElement('div');reasoning.className='live-reasoning';reasoning.hidden=true;
      box.append(title, body, note,reasoning); streamFeed.append(box);
    }
    box.dataset.generationId = packet.generation.id;
    box.dataset.hasOutput = packet.generation.text || packet.generation.reasoning ? 'true' : 'false';
    box.children[0].textContent = packet.generation.reasoning&&!packet.generation.text?'النموذج يفكّر الآن':'صياغة الرد وتجهيز العمليات';
    // Never parse unfinished Markdown/HTML or tool JSON as executable content.
    box.children[1].textContent = packet.generation.text || (packet.generation.reasoning ? 'النموذج يفكّر…' : 'تجهيز سياق الطلب قبل أول توكن…');
    box.children[2].textContent = packet.generation.tools?.filter(Boolean).length
      ? 'تجهيز الأدوات: ' + packet.generation.tools.filter(Boolean).join('، ') + ' · التنفيذ بعد اكتمال الرد'
      : 'عرض أثناء التوليد · يعتمد التسليم على الفحص النهائي';
    const thinking=box.children[3];thinking.hidden=!packet.generation.reasoning;
    thinking.textContent=(packet.generation.reasoning_truncated?'مقتطف من نهاية التفكير:\n':'تفكير النموذج:\n')+(packet.generation.reasoning||'');
    if (nearBottom) streamFeed.scrollTop = streamFeed.scrollHeight;
  }
  const previousRender = render;
  render = function (s) { previousRender(s); paint(latest); };

  async function connect() {
    while (!stopped) {
      try {
        controller = new AbortController();
        const sentToken = token;
        const response = await fetch('/api/live', {cache:'no-store', credentials:'same-origin',
          headers:{'X-Local-Token':sentToken}, signal:controller.signal});
        if (response.status === 403) { await refreshLocalSession(sentToken); continue; }
        if (!response.ok) throw Error('live connection unavailable');
        connected = true; paint(latest);
        const reader = response.body.getReader(), decoder = new TextDecoder();
        let buffer = '';
        while (!stopped) {
          const {value, done} = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, {stream:true});
          let end;
          while ((end = buffer.indexOf('\n\n')) >= 0) {
            const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
            if (!frame.startsWith('data: ')) continue;
            const packet = JSON.parse(frame.slice(6)), old = latest;
            latest = packet;
            if (packet.project === state?.project && packet.session_id === state?.session_id) {
              if (packet.job?.phase) $('phase').textContent = packet.job.phase;
            }
            paint(packet);
            if (old && (old.job?.status !== packet.job?.status || old.session_id !== packet.session_id ||
                JSON.stringify(old.event) !== JSON.stringify(packet.event))) poll();
          }
          if (buffer.length > 2 * 1024 * 1024) throw Error('oversized live frame');
        }
      } catch (error) { if (stopped) break; }
      connected = false; paint(latest);
      await new Promise(resolve => setTimeout(resolve, 1800));
    }
  }

  memoryButton.onclick = async () => {
    try {
      memoryScope = {project:state.project, session_id:state.session_id};
      const value = await api('/api/requirements', memoryScope);
      memoryRevision = value.revision; memoryOffset = value.next_offset;
      $('requirementsBrief').value = value.brief;
      $('requirementsHistory').textContent = value.content;
      $('moreRequirements').disabled = !value.more;
      $('requirementsStatus').textContent = value.count + ' طلبًا محفوظًا · يُحفظ الملخص بعد الضغط على حفظ';
      $('saveRequirements').disabled = state.job?.status === 'running' || state.repair_run?.status === 'running';
      $('requirementsDialog').showModal();
    } catch (error) { window.console.error(error); $('phase').textContent = error.message; }
  };
  $('closeRequirements').onclick = () => $('requirementsDialog').close();
  $('saveRequirements').onclick = async () => {
    try {
      const value = await api('/api/requirements', {...memoryScope, revision:memoryRevision, brief:$('requirementsBrief').value});
      memoryRevision = value.revision; $('requirementsStatus').textContent = 'حُفظت المتطلبات مع نسخة من الملخص السابق';
    } catch (error) { $('requirementsStatus').textContent = error.message; }
  };
  $('moreRequirements').onclick = async () => {
    try {
      const value = await api('/api/requirements', {...memoryScope, offset:memoryOffset});
      if (value.revision !== memoryRevision) throw Error('تغير السجل؛ أعد فتح الذاكرة');
      memoryOffset = value.next_offset;
      $('requirementsHistory').textContent += value.content;
      $('moreRequirements').disabled = !value.more;
    } catch (error) { $('requirementsStatus').textContent = error.message; }
  };
  window.addEventListener('pagehide', () => { stopped = true; controller?.abort(); });
  window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; connect(); } });
  connect();
})();
