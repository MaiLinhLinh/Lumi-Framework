import { createInterface } from 'node:readline';
import { z } from 'zod/v4';
import { corePrompt, parseProgram, mergePatch, describeContent, generateAssemblyPrompt, mergeContent, mergeContents, ProgramError } from './program';

// stdout is reserved for versioned JSON-lines replies. No session state here.
const lines=createInterface({input:process.stdin,crlfDelay:Infinity});
for await (const line of lines) {
  let request:any;
  try {
    request=JSON.parse(line);
    if (request.protocol!==1 || !Number.isInteger(request.id)) throw Error('Invalid protocol envelope');
    let result:unknown;
    if(request.method==='describe_runtime') result={protocol:1,pid:process.pid,core_prompt:corePrompt};
    else if(request.method==='generate_assembly_prompt') result=generateAssemblyPrompt(request.components);
    else if(['parse_program','merge_patch','describe_content','merge_content','merge_contents'].includes(request.method)) {
      try {result=request.method==='parse_program'?parseProgram(request.source)
        :request.method==='merge_patch'?mergePatch(request.source,request.patch)
        :request.method==='describe_content'?describeContent(request.source,request.slot)
        :request.method==='merge_content'?mergeContent(request.source,request.patch,request.slot)
        :mergeContents(request.source,request.patch,request.slots);}
      catch(error) {
        if (error instanceof ProgramError) throw error;
        if (error instanceof z.ZodError) throw new ProgramError('parse_exception',{issues:error.issues});
        throw new ProgramError('parse_exception',String(error instanceof Error?error.message:error));
      }
    }
    else throw Error('Unsupported processor method');
    process.stdout.write(JSON.stringify({protocol:1,id:request.id,ok:true,result})+'\n');
  } catch(error) {
    const validation=error instanceof ProgramError;
    const diagnostic=validation?error.diagnostic:{code:'processor_error',message:String(error instanceof Error?error.message:error)};
    process.stdout.write(JSON.stringify({protocol:1,id:request?.id??null,ok:false,error:{kind:validation?'validation':'infrastructure',...diagnostic}})+'\n');
  }
}
