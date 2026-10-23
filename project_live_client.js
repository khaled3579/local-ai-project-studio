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
    memoryButton.disabled = !state.project;
    $('saveRequirements').disabled = state.job?.status === 'running' || state.repair_run?.status === 'running' || (memoryScope && (memoryScope.project !== state.project || memoryScope.session_id !== state.session_id));
    liveBadge.textContent = connected ? 'بث مباشر' : 'تحديث دوري · إعادة اتصال البث';
    const same = packet && packet.project === state.project && packet.session_id === state.session_id;
    paintUsage(same && packet.job?.id === state.job?.id ? packet.job : state.job);
    const job = same && packet.job?.id === state.job?.id ? packet.job : state.job;
    acceptanceEvidence = typeof job?.delivery_evidence?.requirements_coverage === 'object' ? job.delivery_evidence : null;
    acceptanceButton.disabled = !acceptanceEvidence;
    acceptanceButton.textContent = acceptanceEvidence
      ? 'أدلة القبول · ' + acceptanceEvidence.requirements_coverage.items.filter(row => row.passed).length + '/' + acceptanceEvidence.requirements_coverage.items.length
      : 'أدلة القبول';
    const old = $('liveGeneration');
    if (!same || !packet.generation || packet.job?.status !== 'running') {
      old?.remove(); return;
    }
    const nearBottom = $('chat').scrollHeight - $('chat').scrollTop - $('chat').clientHeight < 140;
    let box = old;
    if (!box) {
      box = document.createElement('div'); box.id = 'liveGeneration'; box.className = 'message assistant';
      const title = document.createElement('strong'); title.textContent = 'المساعد · يولّد الآن';
      const body = document.createElement('div'); body.style.whiteSpace = 'pre-wrap';
      const note = document.createElement('small'); note.className = 'hint';
      box.append(title, body, note); $('chat').append(box);
    }
    // Never parse unfinished Markdown/HTML or tool JSON as executable content.
    box.children[1].textContent = packet.generation.text || 'جارٍ تجهيز الرد…';
    box.children[2].textContent = packet.generation.tools?.filter(Boolean).length
      ? 'تجهيز الأدوات: ' + packet.generation.tools.filter(Boolean).join('، ') + ' · التنفيذ بعد اكتمال الرد'
      : 'عرض أثناء التوليد · يعتمد التسليم على الفحص النهائي';
    if (nearBottom || !old) $('chat').scrollTop = $('chat').scrollHeight;
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
