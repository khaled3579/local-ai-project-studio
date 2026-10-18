/* Read-only model summary; never alters generation or model settings. */
(()=>{
 const bar=document.getElementById('modelInfoBar');if(!bar)return;
 const fields={};
 const number=new Intl.NumberFormat('en-US',{maximumFractionDigits:1});
 function cell(key,label){const item=document.createElement('span');item.className='model-fact';const caption=document.createElement('span');caption.className='model-fact-label';caption.textContent=label;const value=document.createElement('bdi');value.textContent='—';item.append(caption,value);bar.append(item);fields[key]=value;return item;}
 const nameCell=cell('name','النموذج');nameCell.classList.add('model-name');
 cell('size','الحجم');cell('speed','التوليد');cell('mtp','MTP');cell('experts','الخبراء');cell('context','السياق');
 const gpuRow=document.createElement('div');gpuRow.className='model-gpus';gpuRow.setAttribute('aria-label','حالة بطاقات الرسوم');bar.append(gpuRow);
 function gpus(data){
  gpuRow.replaceChildren();const rows=data.gpus||[];
  if(!rows.length){gpuRow.textContent='قراءات بطاقات NVIDIA غير متاحة';return;}
  const stale=data.gpu_stale||!data.gpu_updated||Date.now()/1000-data.gpu_updated>10;
  gpuRow.dataset.stale=String(stale);
  const metric=(value,suffix)=>typeof value==='number'?number.format(value)+suffix:'غير متاح';
  for(const g of rows){const item=document.createElement('span');item.className='model-gpu';item.title=(g.name||'NVIDIA')+' · '+(stale?'آخر قراءة محفوظة':'تحديث خلفي كل 3 ثوانٍ')+(data.gpu_error?' · '+data.gpu_error:'');const label=document.createElement('bdi');label.className='gpu-name';label.dir='ltr';label.textContent=(g.name||'NVIDIA').replace(/^NVIDIA GeForce /,'')+' #'+g.index;item.append(label);const used=g.memory_mib==null?'—':number.format(g.memory_mib/1024),total=g.memory_total_mib==null?'—':number.format(g.memory_total_mib/1024);for(const [caption,value] of [['الذاكرة',used+' / '+total+' GiB'],['الاستخدام',metric(g.util_percent,'%')],['الحرارة',metric(g.temperature_c,'°C')]]){const part=document.createElement('span');part.className='gpu-metric';const title=document.createElement('span');title.className='model-fact-label';title.textContent=caption;const reading=document.createElement('bdi');reading.dir='ltr';reading.textContent=value;part.append(title,reading);item.append(part);}gpuRow.append(item);}
  const freshness=document.createElement('span');freshness.className='gpu-freshness';freshness.textContent=stale?'قراءات قديمة · تعذّر التحديث':'تحديث كل 3 ثوانٍ';gpuRow.append(freshness);
 }
 function render(data){
  gpus(data);
  const m=data.model;bar.dataset.state=data.status;
  if(!m){fields.name.textContent=({loading:'جارٍ تحميل النموذج',stopped:'لم يُحمّل نموذج',failed:'تعذّر تحميل النموذج',unavailable:'بيانات النموذج غير متاحة'})[data.status]||'بيانات النموذج غير متاحة';fields.name.title='';for(const k of ['size','speed','mtp','experts','context'])fields[k].textContent='—';return;}
  fields.name.textContent=m.filename||m.name;fields.name.title=m.name+'\n'+m.filename;
  fields.size.textContent=m.bytes>0?number.format(m.bytes/1024**3)+' GiB':'غير متاح';
  fields.size.title='حجم ملفات أوزان النموذج على القرص، دون ملف الرؤية أو ذاكرة GPU'+(m.parameters?' · '+number.format(m.parameters/1e9)+'B معامل':'');
  const speed=m.speed||{};fields.speed.textContent=speed.tokens_per_second>0?number.format(speed.tokens_per_second)+' tok/s'+(speed.phase==='live'?' · مباشر':' · آخر رد'):'بانتظار التوليد';
  fields.speed.title='سرعة فك الرموز من قياس المحرك؛ لا تشمل معالجة الطلب الأولية';
  fields.mtp.textContent=m.mtp_supported===false?'غير مدعوم':m.mtp_enabled?'مفعّل':m.mtp_supported===true?'مدعوم · معطّل':'الدعم غير معلوم';
  fields.experts.textContent=m.experts_total===0?'كثيف · بلا خبراء':m.experts_total>0?(m.experts_active>0?m.experts_active+' نشط / ':'')+m.experts_total+' إجمالي':'غير مذكور';
  fields.experts.title='عدد الخبراء وإجماليهم من بيانات GGUF؛ لا يُستنتج من اسم الملف';
  fields.context.textContent=m.context?number.format(m.context):'غير متاح';
 }
 async function refresh(){try{const response=await fetch('/api/model-info',{cache:'no-store',signal:AbortSignal.timeout(4000)});if(!response.ok)throw Error();render(await response.json());}catch{render({status:'unavailable',model:null});}finally{setTimeout(refresh,3000);}}
 refresh();
})();
