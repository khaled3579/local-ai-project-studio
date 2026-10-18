/* On-demand maintenance, using the same authenticated/scoped API as the editor. */
(() => {
 const button=document.createElement('button');button.id='maintenanceOpen';button.textContent='النظام والصيانة';document.querySelector('.header-actions').append(button);
 const dialog=document.createElement('dialog');dialog.id='maintenanceDialog';dialog.setAttribute('aria-label','النظام والاحتفاظ بالبيانات');
 dialog.innerHTML=`<div class="wb-toolbar"><strong>النظام والاحتفاظ بالبيانات</strong><button id="maintenanceClose">إغلاق</button></div>
 <p id="maintenanceVersion"></p><p id="maintenanceProtected"></p>
 <div class="wb-toolbar"><label>تدوير سجل المشروع MB <input id="maintenanceLog" type="number" min="1" max="64" style="width:5em"></label><label>سجلات قريبة <input id="maintenanceHot" type="number" min="1" max="100" style="width:5em"></label><label>عمر التصدير بالأيام <input id="maintenanceDays" type="number" min="1" max="3650" style="width:5em"></label></div>
 <div class="wb-toolbar"><button id="maintenancePolicy">حفظ السياسة</button><button id="maintenanceDoctor">فحص بيئة التشغيل</button><button id="maintenancePlan">معاينة الأرشفة</button><button id="maintenanceApply" disabled>تطبيق الأرشفة المعاينة</button></div>
 <p id="maintenanceStatus" role="status"></p><pre id="maintenanceDetails" style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:35vh;overflow:auto" dir="auto"></pre>
 <div class="wb-toolbar"><select id="maintenanceArchives" aria-label="الأرشيف المحفوظ"></select><button id="maintenanceRestore" disabled>استعادة الأرشيف</button></div><p>للترقية شغّل Update-App.ps1 مع ملف الإصدار وبصمته. للاسترجاع شغّل Rollback-App.ps1. تبقى بياناتك الأحدث محفوظة.</p>`;
 document.body.append(dialog);dialog.style.maxWidth='min(900px,94vw)';dialog.style.width='90vw';
 const el=id=>document.getElementById(id);let scope=null,review=null,waiting=false;
 const call=(action,extra={})=>api('/api/maintenance',{...scope,action,...extra});
 const detail=value=>el('maintenanceDetails').textContent=typeof value==='string'?value:JSON.stringify(value,null,2);
 async function load(){const r=await call('status');el('maintenanceVersion').textContent='الإصدار '+r.version+' · بيانات v'+r.schema+' · '+r.python;el('maintenanceProtected').textContent=r.protected;el('maintenanceLog').value=r.policy.log_mb;el('maintenanceHot').value=r.policy.hot_archives;el('maintenanceDays').value=r.policy.export_days;detail({storage:r.storage,system_logs:r.system_logs,upgrade_backups:r.upgrade_backups});el('maintenanceArchives').replaceChildren(...r.archives.map(a=>{const o=document.createElement('option');o.value=a.id;o.textContent=new Date(a.created*1000).toLocaleString()+' · '+(a.bytes/1024).toFixed(1)+' KB';return o}));el('maintenanceRestore').disabled=!r.archives.length;}
 async function run(fn){if(waiting)return;waiting=true;el('maintenanceStatus').textContent='جارٍ التنفيذ…';for(const b of dialog.querySelectorAll('button'))if(b.id!=='maintenanceClose')b.disabled=true;try{await fn();el('maintenanceStatus').textContent='اكتملت العملية'}catch(e){el('maintenanceStatus').textContent=e.message}finally{waiting=false;for(const id of ['maintenancePolicy','maintenanceDoctor','maintenancePlan'])el(id).disabled=false;el('maintenanceApply').disabled=!review?.files.length;el('maintenanceRestore').disabled=!el('maintenanceArchives').options.length;}}
 button.onclick=()=>{scope={project:state.project,session_id:state.session_id};review=null;dialog.showModal();run(load)};
 el('maintenanceClose').onclick=()=>dialog.close();
 el('maintenancePolicy').onclick=()=>run(async()=>{await call('policy',{policy:{version:1,log_mb:Number(el('maintenanceLog').value),hot_archives:Number(el('maintenanceHot').value),export_days:Number(el('maintenanceDays').value)}});review=null;await load()});
 el('maintenanceDoctor').onclick=()=>run(async()=>{const r=await call('doctor');detail(r);if(!r.success)throw Error('توجد مشكلة في بيئة التشغيل؛ راجع تفاصيل الفحص')});
 el('maintenancePlan').onclick=()=>run(async()=>{review=await call('plan');detail({message:'أرشفة قابلة للاستعادة فقط؛ لا حذف للمحادثات أو ملفات المشروع',files:review.files,bytes:review.bytes})});
 el('maintenanceApply').onclick=()=>run(async()=>{const r=await call('archive',{id:review.id});review=null;await load();detail(r)});
 el('maintenanceRestore').onclick=()=>run(async()=>{const r=await call('restore',{id:el('maintenanceArchives').value});await load();detail(r)});
})();
