(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.EditorDrafts=factory();})(typeof window==='object'?window:this,function(){
  class EditorDrafts {
    constructor(storage){this.storage=storage;this.pending=new Map();this.error=null;}
    key(project,path){return 'studio.editor.v1:'+JSON.stringify([project,path]);}
    read(project,path){
      const key=this.key(project,path);if(this.pending.has(key))return this.pending.get(key);
      try{const value=JSON.parse(this.storage.getItem(key)||'null');
        if(value&&value.project===project&&value.path===path&&typeof value.text==='string'&&typeof value.base==='string'&&typeof value.sha256==='string')return value;
      }catch(error){this.error=error.message;}return null;
    }
    stage(project,path,sha256,base,text){this.pending.set(this.key(project,path),{project,path,sha256,base,text,updated:Date.now()});}
    flush(){
      let success=true;
      for(const [key,value] of this.pending){try{
        if(value.text===value.base)this.storage.removeItem(key);else this.storage.setItem(key,JSON.stringify(value));
        this.pending.delete(key);
      }catch(error){this.error=error.message;success=false;}}
      return success;
    }
    committed(project,path,sha256,text){this.stage(project,path,sha256,text,text);return this.flush();}
  }
  return EditorDrafts;
});
