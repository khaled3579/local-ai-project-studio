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

  function paint(packet) {
    if (!state) return;
    memoryButton.disabled = !state.project;
    $('saveRequirements').disabled = state.job?.status === 'running' || state.repair_run?.status === 'running' || (memoryScope && (memoryScope.project !== state.project || memoryScope.session_id !== state.session_id));
    liveBadge.textContent = connected ? 'بث مباشر' : 'تحديث دوري · إعادة اتصال البث';
    const same = packet && packet.project === state.project && packet.session_id === state.session_id;
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
