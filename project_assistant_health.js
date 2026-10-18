'use strict';
const fs=require('node:fs'),path=require('node:path');
async function main(){
 const rows={};
 for(const name of ['playwright','pyright','typescript'])rows[name]=require('./runtime/assistant/node_modules/'+name+'/package.json').version;
 const {chromium}=require('./runtime/assistant/node_modules/playwright');
 process.env.PLAYWRIGHT_BROWSERS_PATH=path.join(__dirname,'runtime/browser');
 const {execFileSync}=require('node:child_process');
 // A separate process loads Playwright after its bundled browser path is set.
 if(process.argv.includes('--launch')){
  const browser=await chromium.launch({headless:true,args:['--disable-gpu'],timeout:20000});const page=await browser.newPage();await page.setContent('<title>Assistant health</title><button>Ready</button>');rows.browser=await page.title();await browser.close();
 }else if(process.argv.includes('--full')){
  const result=execFileSync(process.execPath,[__filename,'--launch'],{env:{...process.env},windowsHide:true,timeout:25000});rows.launch=JSON.parse(result.toString());
 }
 rows.browser_files=fs.existsSync(path.join(__dirname,'runtime/browser'));process.stdout.write(JSON.stringify(rows));
}
main().catch(e=>{process.stderr.write(e.message);process.exitCode=1});
