import { autoClose, createParser, evaluateRoot, generateSystemPrompt, mergeStatements, parseExpression, split, tokenize, walkAST,
  type ElementNode, type LibrarySpec, type OpenUIError } from '@openuidev/lang-core';
import { z } from 'zod/v4';
import { createLumiLibrary } from '../src/surface/library';
import { modules } from 'virtual:lumi-widget-modules';

export const library = createLumiLibrary(modules);
const schema = library.toJSONSchema(), parser = createParser(schema,'SurfaceProgram');
const names = new Map([...modules].map(([id, module])=>[module.widget.name,id]));
const core = new Set(['SurfaceProgram','Slot','Pending']);
const spec = library.toSpec();
// Discovery augments this fixed prompt through tool messages, never by replacing it.
const coreSpec: LibrarySpec = {...spec, components:Object.fromEntries(Object.entries(spec.components).filter(([name])=>core.has(name)))};
export const corePrompt = generateSystemPrompt({library:coreSpec,promptOptions:{
  toolCalls:false, bindings:false, builtinFunctions:false, editMode:true,
  additionalRules:[
    'Create a complete SurfaceProgram when no accepted program is provided. Otherwise return only changed statements, merged against the accepted program. No Query, Mutation, actions or $state.',
    'Widget signatures are supplied by describe_widget tool messages and extend this core library. Describe each chosen widget and child before use; do not invent a signature.',
    'Only @async_task declares work. It returns data; Python owns scheduling. No work executes while evaluating or rendering.',
    'Return only OpenUI Lang statements. Use a named statement for each root Slot; root references every slot and task. No markdown or JSON output.',
  ],
}});

export class ProgramError extends Error {
  readonly diagnostic:Record<string,unknown>;
  constructor(public code:string, data:string|Record<string,unknown>) {
    super(typeof data==='string'?data:JSON.stringify(data));
    this.diagnostic={code,...(typeof data==='string'?{message:data}:data)};
  }
}
function fail(code:string,data:string|Record<string,unknown>):never {throw new ProgramError(code,data);}
function element(value:unknown):value is ElementNode {return !!value && typeof value==='object' && (value as ElementNode).type==='element';}
// OpenUI .ref schemas describe referenced props; parser nodes also carry an
// ElementNode envelope. Validate the props view without losing that envelope.
function propsView(value:unknown):any {
  if (element(value)) return propsView(value.props);
  if (Array.isArray(value)) return value.map(propsView);
  if (value && typeof value==='object') return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,propsView(v)]));
  return value;
}
function hasElement(value:unknown):boolean {
  return element(value) || Array.isArray(value)&&value.some(hasElement)
    || !!value&&typeof value==='object'&&Object.values(value).some(hasElement);
}
function validateTree(value:unknown):void {
  if (Array.isArray(value)) {value.forEach(validateTree);return;}
  if (!value || typeof value!=='object') return;
  if (element(value)) {
    const def = library.components[value.typeName];
    if (!def || value.partial) fail('invalid_component',`Incomplete or unknown component ${value.typeName}`);
    const result = z.safeParse(def.props,propsView(value.props));
    if (!result.success) fail('invalid_props',{statement:value.statementId||value.typeName,issues:result.error.issues});
    for (const [key,normalized] of Object.entries(result.data as Record<string,unknown>))
      if (!hasElement(value.props[key])) value.props[key]=normalized;
  }
  Object.values(element(value)?value.props:value).forEach(validateTree);
}
function content(value:unknown, used:Set<string>, child=false):Record<string,unknown> {
  if (!element(value)) fail('invalid_content','Slot/Pending children must be component expressions.');
  const pending=value.typeName==='Pending', id=pending?String(value.props.widget_id):names.get(value.typeName);
  if (!id || !modules.has(id)) fail('invalid_widget',`Not a registered content widget: ${value.typeName}`);
  used.add(id);
  const props={...(pending?value.props.props:value.props) as Record<string,unknown>};
  const children=(pending?value.props.children:props.children) as unknown[]|undefined;
  if (!pending) delete props.children;
  const result:Record<string,unknown>={widget_id:id,props};
  if (children?.length) {
    if (child) fail('invalid_children','Nested child containers exceed the current Lumi child contract.');
    result.children=children.map(node=>content(node,used,true));
  }
  // Do not serialize unevaluated expressions or elements into plain props.
  function plain(v:unknown):boolean {
    return v===null || ['string','boolean','number'].includes(typeof v)
      || Array.isArray(v)&&v.every(plain)
      || !!v&&typeof v==='object'&&!('type' in v && (v as any).type==='element')&&!('k' in v)&&Object.values(v).every(plain);
  }
  if (!plain(props)) fail('invalid_props','Widget props must evaluate to plain JSON data.');
  return result;
}
function statementsOf(source:string, requireRoot=false) {
  if (typeof source!=='string' || !source.trim() || source.length>512_000) fail('invalid_source','Expected a nonempty OpenUI program under 512 KB.');
  if (source.trimStart().startsWith('```') || autoClose(source).wasIncomplete) fail('incomplete_program','Return complete statements without markdown fences.');
  const tokens=tokenize(source), statements=split(tokens), ids=statements.map(stmt=>stmt.id);
  const newline=tokenize('\n')[0].t, eof=tokens.at(-1)!.t;
  if (tokens.filter(token=>token.t!==newline && token.t!==eof).length!==statements.reduce((sum,stmt)=>sum+2+stmt.tokens.length,0))
    fail('invalid_statements','Only named OpenUI statements are accepted; remove unparsed prose or incomplete declarations.');
  if (!ids.length || new Set(ids).size!==ids.length || requireRoot&&!ids.includes('root')) fail('invalid_statements','Declare unique named statements, including root for a complete program.');
  for (const stmt of statements) {
    if (stmt.id.startsWith('$')) fail('unsupported_expression','$state is not part of the Lumi surface plan contract.');
    walkAST(parseExpression(stmt.tokens),node=>{
      if (['StateRef','RuntimeRef','Assign'].includes(node.k)
        || node.k==='Comp' && (['Query','Mutation','Action'].includes(node.name)||node.fn && node.name!=='async_task'))
        fail('unsupported_expression','Only static widget content and @async_task declarations are accepted.');
    });
  }
  return statements;
}
export function parseProgram(source:string) {
  const statements=statementsOf(source,true);
  const parsed=parser.parse(source);
  if (!parsed.root || parsed.meta.incomplete || parsed.meta.unresolved.length || parsed.meta.errors.length || parsed.meta.orphaned.length)
    fail('parse_failed',{meta:parsed.meta});
  const errors:OpenUIError[]=[];
  const functions=Object.fromEntries(Object.entries(library.functions||{}).map(([name,def])=>[name,{...schema.functions![name]!,fn:(args:unknown)=>{
    const input=z.parse(def.params,args), result=def.fn(input);
    return def.returns?z.parse(def.returns,result):result;
  }}]));
  const root=evaluateRoot(parsed,{library,store:null,errors,ctx:{getState:()=>null,resolveRef:()=>null,functions}});
  if (errors.length) fail('evaluation_failed',{errors});
  if (!root || root.typeName!=='SurfaceProgram') fail('evaluation_failed','Expected SurfaceProgram root');
  validateTree(root);
  const used=new Set<string>(), slots:string[]=[], seen=new Set<string>(), stageSlots:Record<string,unknown>[]=[];
  const blocks=(root.props.blocks as unknown[]).map(node=>{
    if (!element(node)||node.typeName!=='Slot'||!node.statementId||seen.has(node.statementId)) fail('invalid_slot','Each root Slot must have a unique named statement.');
    seen.add(node.statementId);slots.push(node.statementId);
    const p=node.props, parsedContent=content(p.content,used);
    const block={...parsedContent,grid:p.grid} as Record<string,unknown>;
    const ast=parseExpression(statements.find(stmt=>stmt.id===node.statementId)!.tokens);
    if (p.slot_role && (ast.k!=='Comp'||ast.name!=='Slot'||ast.args[1]?.k!=='Ref'))
      fail('content_reference_required',`${node.statementId}: task-owned content must be a separate named statement referenced by Slot.`);
    for (const key of ['slot_role','description','initial_visibility','initial_state']) if (p[key]!=null) block[key]=p[key];
    stageSlots.push({
      statement_id:node.statementId, grid:p.grid,
      content:{...parsedContent,pending:element(p.content)&&p.content.typeName==='Pending'},
      slot_role:p.slot_role??null, description:p.description??null,
      initial_visibility:p.initial_visibility??'visible', initial_state:p.initial_state??{},
    });
    return block;
  });
  return {plan:{action:'create_surface_plan',template_description:root.props.template_description??null,surface:{blocks},tasks:root.props.tasks||[]},
    bindings:{slots,used_widgets:[...used].sort(),tasks: (()=>{
      const ast=parseExpression(statements.find(stmt=>stmt.id==='root')!.tokens);
      if(ast.k!=='Comp'||ast.name!=='SurfaceProgram')fail('invalid_root','Root must call SurfaceProgram directly.');
      const tasks=ast.args[1];
      if(!tasks)return [];
      if(tasks.k!=='Arr')fail('invalid_tasks','Root tasks must be an array of named task references.');
      return tasks.els.map((node:any)=>{
        if(node.k!=='Ref')fail('invalid_tasks','Each task requires a named statement.');
        return node.n;
      });
    })()},stage:{type:'SurfaceProgram',slots:stageSlots},source};
}

export function mergePatch(source:string,patch:string) {
  parseProgram(source);
  const changed=new Map(statementsOf(patch).map(stmt=>[stmt.id,parseExpression(stmt.tokens)]));
  const result=parseProgram(mergeStatements(source,patch));
  const merged=graph(result.source);
  const previous=graph(source);
  const state_updates:Record<string,Record<string,unknown>>={}, prop_updates:Record<string,string[]>={};
  result.bindings.slots.forEach((slot,index)=>{
    const block=result.plan.surface.blocks[index], ast=merged.get(slot);
    if(changed.has(slot)) {
      const state={...(block.initial_state as Record<string,unknown>|undefined)};
      if(block.initial_visibility!=null)state.visibility=block.initial_visibility;
      if(Object.keys(state).length)state_updates[slot]=state;
    }
    // Content may be inline or supplied by a changed referenced statement.
    // A Slot layout edit that reuses unchanged content does not reset its value.
    if(ast?.k!=='Comp'||ast.name!=='Slot')return;
    const old=previous.get(slot);
    let contentChanged=changed.has(slot)&&(ast.args[1]?.k!=='Ref'
      || old?.k!=='Comp'||JSON.stringify(old.args[1])!==JSON.stringify(ast.args[1]));
    const visited=new Set<string>();
    const visit=(node:any)=>{
      walkAST(node,child=>{
        if(child.k==='Ref'&&!visited.has(child.n)) {
          visited.add(child.n);
          if(changed.has(child.n))contentChanged=true;
          const dependency=merged.get(child.n);
          if(dependency)visit(dependency);
        }
      });
    };
    if(ast.args[1])visit(ast.args[1]);
    if(contentChanged)prop_updates[slot]=Object.keys(block.props as Record<string,unknown>);
  });
  return {...result,state_updates,prop_updates};
}

function graph(source:string) {
  return new Map(statementsOf(source).map(stmt=>[stmt.id,parseExpression(stmt.tokens)]));
}
function contentScope(source:string,slot:string) {
  const asts=graph(source), ast=asts.get(slot);
  if (!ast||ast.k!=='Comp'||ast.name!=='Slot'||ast.args[1]?.k!=='Ref')
    fail('invalid_content_scope','The assigned Slot must reference a named content statement.');
  const id=ast.args[1].n, allowed=new Set<string>(), queue=[id];
  while(queue.length){
    const name=queue.pop()!;
    if(allowed.has(name))continue;
    allowed.add(name);
    const node=asts.get(name);
    if(!node)fail('invalid_content_scope',`Missing content statement ${name}`);
    walkAST(node,child=>{if(child.k==='Ref')queue.push(child.n);});
  }
  return {asts,id,allowed};
}
export function describeContent(source:string,slot:string) {
  const parsed=parseProgram(source), {asts,id,allowed}=contentScope(source,slot);
  const index=parsed.bindings.slots.indexOf(slot);
  if(index<0)fail('invalid_content_scope','Assigned Slot is not in the root.');
  let temporary='__lumi_content_scope';
  while(asts.has(temporary))temporary+='_';
  const tail=`${temporary} = ${id}`;
  // Let official merge prune the graph from a temporary root. Remove only our
  // exact appended statement; no source parser or merger is implemented here.
  const pruned=mergeStatements(source,tail,temporary);
  if(!pruned.endsWith(tail))fail('invalid_content_scope','Cannot extract scoped content.');
  const block=parsed.plan.surface.blocks[index];
  const ids=new Set([String(block.widget_id),...((block.children as any[]|undefined)||[]).map(child=>String(child.widget_id))]);
  const components=Object.fromEntries(Object.entries(spec.components)
    .filter(([name])=>name==='Pending'||ids.has(names.get(name)||''))
    .map(([name,definition])=>[name,{...definition,props_schema:schema.$defs?.[name]}]));
  return {content_statement:id,content_source:pruned.slice(0,-tail.length).trim(),allowed_statements:[...allowed],
    content_signature:JSON.stringify([...allowed].sort().map(name=>[name,asts.get(name)])),
    widget_definitions:components};
}
export function generateAssemblyPrompt(componentNames:string[]) {
  if(!Array.isArray(componentNames)||!componentNames.length
    ||componentNames.some(name=>typeof name!=='string'||!spec.components[name]||core.has(name)&&name!=='Pending'))
    fail('invalid_content_scope','Assembly prompt requires registered content components.');
  const selected=new Set(componentNames);
  const scopedSpec:LibrarySpec={...spec,
    components:Object.fromEntries(Object.entries(spec.components).filter(([name])=>selected.has(name))),
    functions:{},actions:{},
  };
  return {syntax_prompt:generateSystemPrompt({library:scopedSpec,promptOptions:{
    toolCalls:false,bindings:false,builtinFunctions:false,editMode:true,
    additionalRules:[
      'Assembly always edits an existing accepted_program. Instructions about root and full-program streaming describe that existing program, not the response. Never emit root, SurfaceProgram or Slot statements.',
      'Return only changed content statements for the editable_slots supplied by the backend. Do not declare tasks, functions, actions, Query, Mutation or $state; no discovery tools are available or needed.',
      'Use the positional component signatures above. A colon belongs inside an object literal, never between a component parameter name and its value.',
      'Keep the existing widget types, layout, metadata, state, children and collection cardinality. Fill only missing content using the task output assigned to each editable slot.',
      'Complete all editable slots supported by their own output_data in one content patch. Do not omit required changes just to shorten the patch. Leave only genuinely unsupported content unresolved.',
      'If no supported changes are available, return an empty response rather than the root statement. Never output markdown fences, prose or an entire program.',
    ],
  }})};
}
export function mergeContent(source:string,patch:string,slot:string) {
  return mergeContents(source,patch,[slot]);
}
export function mergeContents(source:string,patch:string,slots:string[]) {
  const before=parseProgram(source);
  if(!Array.isArray(slots)||!slots.length||new Set(slots).size!==slots.length)
    fail('patch_scope','Expected a nonempty list of unique editable slots.');
  const scopes=slots.map(slot=>contentScope(source,slot));
  const asts=scopes[0].asts, allowed=new Set(scopes.flatMap(scope=>[...scope.allowed]));
  const indices=new Set(slots.map(slot=>{
    const index=before.bindings.slots.indexOf(slot);
    if(index<0)fail('patch_scope','Assigned Slot is not in the root.');
    return index;
  }));
  const updates=statementsOf(patch);
  const patchIds=new Set(updates.map(stmt=>stmt.id));
  for(const stmt of updates){
    if(asts.has(stmt.id)&&!allowed.has(stmt.id))fail('patch_scope',`Statement ${stmt.id} is outside assigned content.`);
    const node=parseExpression(stmt.tokens);
    if(node.k==='Null')fail('patch_scope','Assembly cannot delete content statements.');
    if(JSON.stringify(node)===JSON.stringify(asts.get(stmt.id)))fail('unchanged_statement',`Omit unchanged statement ${stmt.id}.`);
    walkAST(node,child=>{
      if(child.k==='Comp'&&(core.has(child.name)&&child.name!=='Pending'||child.fn))
        fail('patch_scope','Assembly cannot declare root, Slot or task functions.');
      if(child.k==='Ref'&&!allowed.has(child.n)&&!patchIds.has(child.n))fail('patch_scope',`Reference ${child.n} is outside assigned content.`);
    });
  }
  const merged=mergeStatements(source,patch), after=parseProgram(merged);
  if(JSON.stringify(before.bindings.slots)!==JSON.stringify(after.bindings.slots)
    ||JSON.stringify(before.plan.tasks)!==JSON.stringify(after.plan.tasks)
    ||before.plan.template_description!==after.plan.template_description)
    fail('patch_scope','Patch changed root, tasks or slot membership.');
  const reachable=graph(merged);
  for(const id of patchIds)if(!reachable.has(id))fail('patch_scope',`Unused patch statement ${id}.`);
  for(let i=0;i<before.plan.surface.blocks.length;i++){
    const a=before.plan.surface.blocks[i], b=after.plan.surface.blocks[i];
    const view=(block:Record<string,unknown>)=>Object.fromEntries(Object.entries(block).filter(([key])=>key!=='props'&&key!=='children'));
    if(JSON.stringify(indices.has(i)?view(a):a)!==JSON.stringify(indices.has(i)?view(b):b))
      fail('patch_scope','Patch affected layout, metadata or content outside assigned slot (possibly through a shared reference).');
  }
  return after;
}
