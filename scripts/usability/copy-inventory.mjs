// Inventory authored Russian UI text; generated API contracts and fixture content excluded.
import ts from '../../frontend/node_modules/typescript/lib/typescript.js';
import {readFile, readdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
const root=process.cwd();
const folders=['frontend/src/pages','frontend/src/ds'];
const files=['frontend/src/App.tsx','frontend/src/ui.tsx','frontend/src/workspace-ui.tsx','frontend/src/WorkspaceNotifications.tsx','frontend/src/api/client.ts','frontend/src/api/workspace.ts'];
for(const folder of folders)for(const name of await readdir(folder))if(/\.tsx?$/.test(name))files.push(path.join(folder,name));
const entries=new Map();
for(const file of files){
 const source=await readFile(file,'utf8');const ast=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,file.endsWith('tsx')?ts.ScriptKind.TSX:ts.ScriptKind.TS);
 function visit(node){
  let text;
  if(ts.isJsxText(node)||ts.isStringLiteral(node)||ts.isNoSubstitutionTemplateLiteral(node))text=node.text;
  else if(ts.isTemplateExpression(node))text=node.getText(ast);
  if(text && /[А-Яа-яЁё]/.test(text)){
   text=text.replace(/\s+/g,' ').trim();
   const line=ast.getLineAndCharacterOfPosition(node.getStart(ast)).line+1;
   const item=entries.get(text)||{id:`T${String(entries.size+1).padStart(3,'0')}`,text,locations:[]};
   item.locations.push({file,line});entries.set(text,item);
  }
  ts.forEachChild(node,visit);
 }
 visit(ast);
}
const value={scope:files,entries:[...entries.values()]};
const output=process.argv[2]||'.cache/usability/copy-inventory.json';
await writeFile(output,JSON.stringify(value,null,2)+'\n');console.log(`${files.length} files, ${entries.size} unique text entries -> ${output}`);
