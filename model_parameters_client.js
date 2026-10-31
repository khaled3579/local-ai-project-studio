/* Model settings are drafts until the user explicitly loads and saves them. */
let parameterSchema=null,parameterModel=null;
const parameterInputs=new Map();
function parameterId(group,key){return 'parameter-'+group+'-'+key}
function parameterConfig(){
  if(!parameterSchema)return {mode:'defaults'};
  if($('parameterMode').value==='defaults')return {mode:'defaults'};
  const value={mode:'custom',generation:{},runtime:{},stop:JSON.parse($('parameterStop').value||'[]')};
  for(const [group,fields] of Object.entries(parameterSchema))for(const [key,spec] of Object.entries(fields)){
    const input=parameterInputs.get(parameterId(group,key));
    if(!input.reportValidity())throw Error('راجع قيمة '+spec.label);
    value[group][key]=typeof spec.default==='number'?Number(input.value):input.value;
  }
  if(!Array.isArray(value.stop)||value.stop.some(s=>typeof s!=='string'||!s))throw Error('تسلسلات التوقف: أدخل قائمة JSON من نصوص غير فارغة، أو []');
  return value;
}
function setParameterBusy(){
  if(!parameterSchema)return;
  const busy=['loading','stopping'].includes(lastState?.status),custom=$('parameterMode').value==='custom';
  $('parameterMode').disabled=busy;$('parameterReset').disabled=busy;
  for(const input of parameterInputs.values())input.disabled=busy||!custom;
  $('parameterStop').disabled=busy||!custom;
}
function renderParameters(model=selected(),force=false){
  if(!parameterSchema||!model)return;
  if(!force&&parameterModel===model.id){setParameterBusy();return}
  parameterModel=model.id;
  const live=[lastState?.active,lastState?.saved].find(c=>c?.model===model.path);
  const saved=live||model.saved_profile;
  const profile=saved?.parameters||{mode:'defaults'};
  $('parameterMode').value=profile.mode||'defaults';
  for(const [group,fields] of Object.entries(parameterSchema))for(const [key,spec] of Object.entries(fields)){
    const input=parameterInputs.get(parameterId(group,key));
    input.value=profile.mode==='custom'&&profile[group]?.[key]!==undefined?profile[group][key]:model.parameter_defaults?.values?.[group]?.[key]??spec.default;
    $(input.id+'-source').textContent=profile.mode==='custom'&&profile[group]?.[key]!==undefined?'تخصيص محفوظ':model.parameter_defaults?.sources?.[group]?.[key]||'افتراضي المحرك';
  }
  $('parameterStop').value=JSON.stringify(profile.stop||[]);
  $('parameterWarnings').textContent=(model.parameter_defaults?.warnings||[]).join(' · ');
  if(saved){
    $('context').value=saved.context;$('mtp').checked=!!saved.mtp&&!!model.mtp;
    if(saved.projector!==undefined)$('projector').value=saved.projector;
    $('expertsUsed').value=saved.experts_used??'';
  }
  setParameterBusy();
}
async function initParameters(){
  const section=document.createElement('section');section.className='panel';
  section.innerHTML='<h2>معاملات النموذج</h2><p class="note">تطبّق قبل التحميل وتُحفظ لكل نموذج. الافتراضيات من GGUF أو generation_config.json؛ القيم غير المعلنة تستخدم افتراضيات المحرك مع إظهار المصدر. ضبط المعاملات يساعد على الاتساق ولا يضمن منع الهلوسة.</p><label class="field" for="parameterMode">وضع الإعدادات</label><select id="parameterMode"><option value="defaults">افتراضيات النموذج / المحرك</option><option value="custom">إعدادات مخصصة</option></select><button id="parameterReset">استعادة الافتراضيات لهذا النموذج</button><p class="note">الاستعادة تجهّز القيم فقط؛ اضغط «تشغيل وحفظ» لتطبيقها. السياق والرؤية وMTP تبقى ضمن إعدادات التشغيل أعلاه.</p><div id="parameterGroups"></div><label class="field" for="parameterStop">تسلسلات التوقف — قائمة JSON</label><textarea id="parameterStop" rows="3" dir="ltr" style="width:100%">[]</textarea><p class="note">‎-1 للإخراج: يستخدم المحرك السياق المتاح؛ مساعد المشاريع يحسب مساحة الرد حسب الطلب. التحكم بالتفكير يعتمد على دعم قالب النموذج. شكل JSON/الأدوات يُدار تلقائيًا في مهام المشاريع.</p><p id="parameterWarnings" class="note warning"></p>';
  document.querySelector('.actions').before(section);
  const presets=document.createElement('select');presets.id='parameterPreset';presets.setAttribute('aria-label','أمثلة إعدادات عامة');
  for(const [name,label] of [['','أمثلة عامة — ليست افتراضيات النموذج'],['code','برمجة ورياضيات'],['extract','استخراج بيانات'],['chat','محادثة عامة'],['creative','كتابة إبداعية']])presets.add(new Option(label,name));
  $('parameterMode').after(presets);
  const response=await fetch('/api/parameter-schema',{cache:'no-store'});
  if(!response.ok)throw Error('تعذّر قراءة إعدادات المعاملات');
  parameterSchema=await response.json();
  for(const [group,fields] of Object.entries(parameterSchema)){
    const details=document.createElement('details');details.open=group==='generation';
    const summary=document.createElement('summary');summary.textContent=group==='generation'?'التوليد والتكرار والتفكير':'GPU والذاكرة والدفعات';details.append(summary);
    for(const [key,spec] of Object.entries(fields)){
      const id=parameterId(group,key),label=document.createElement('label');label.className='field';label.htmlFor=id;label.textContent=spec.label;
      let input;
      if(spec.choices){input=document.createElement('select');for(const option of spec.choices)input.add(new Option(String(option),String(option)))}
      else{input=document.createElement('input');input.type=typeof spec.default==='number'?'number':'text';if(input.type==='number'){input.step=spec.integer?'1':'any';if(spec.minimum!==null)input.min=spec.minimum;if(spec.maximum!==null)input.max=spec.maximum;input.required=true}}
      input.id=id;input.dir='ltr';input.value=spec.default;
      const source=document.createElement('small');source.id=id+'-source';source.className='note';
      input.oninput=()=>{source.textContent='تخصيص المستخدم — غير محفوظ'};
      details.append(label,input,source);parameterInputs.set(id,input);
    }
    $('parameterGroups').append(details);
  }
  $('parameterMode').onchange=()=>setParameterBusy();
  presets.onchange=()=>{
    const values={code:[.1,.95,.05,1],extract:[0,1,0,1],chat:[.7,.9,.05,1.05],creative:[1,.95,.08,1.1]}[presets.value];if(!values)return;
    $('parameterMode').value='custom';
    ['temperature','top_p','min_p','repeat_penalty'].forEach((key,index)=>{const id=parameterId('generation',key);parameterInputs.get(id).value=values[index];$(id+'-source').textContent='مثال عام — غير محفوظ'});
    setParameterBusy();
  };
  $('parameterReset').onclick=()=>{
    const model=selected();if(!model)return;
    $('parameterMode').value='defaults';
    for(const [group,fields] of Object.entries(parameterSchema))for(const [key,spec] of Object.entries(fields)){
      const id=parameterId(group,key);parameterInputs.get(id).value=model.parameter_defaults?.values?.[group]?.[key]??spec.default;
      $(id+'-source').textContent=model.parameter_defaults?.sources?.[group]?.[key]||'افتراضي المحرك';
    }
    $('parameterStop').value='[]';setParameterBusy();
  };
  const apply=$('apply').onclick;
  $('apply').onclick=()=>{try{parameterConfig();apply()}catch(error){showError(error.message)}};
  renderParameters();
}
const parameterReady=initParameters().catch(error=>{showError(error.message);$('apply').disabled=true});
