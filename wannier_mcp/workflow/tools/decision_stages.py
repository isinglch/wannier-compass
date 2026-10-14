#!/usr/bin/env python3
"""Immutable initial/selected/retry snapshots and knowledge hooks; no solvers."""
import argparse
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

import manifold_selection as selection
import material_layout as layout
import orbital_evidence as evidence
import review_diagnostics as diagnostics
import conduction_review as conduction
import selection_gallery as gallery

FORMAT='wannier-decision-stage-v1'
LEGACY_FILES=('inspection.json','review.json','analysis.json','decision.json','selection.json',
       'validation.json','context.json','retrieval.json','selection.png','selection.pdf')
FILES_V1=LEGACY_FILES+('review_diagnostics.json','review_diagnostics.md')
FILES=FILES_V1+('conduction_review.json',)
ROLES=('initial','real_first_try','retry')


def read(path): return json.loads(Path(path).read_text())
def sha(path): return layout.sha(Path(path))
def now(): return datetime.now(timezone.utc).isoformat()
def save(path,obj): layout.write_json(Path(path),obj)


def retrieve(mode, query, snapshot=None):
    """Replaceable backend contract. No vector store/provider is installed."""
    if mode not in ('disabled','experience_assisted') or not isinstance(query,dict):
        raise ValueError('Retrieval needs disabled/experience_assisted and a structured query.')
    if mode=='experience_assisted' and not snapshot:
        raise ValueError('Experience-assisted retrieval must declare a snapshot identifier.')
    receipt=dict(format='wannier-retrieval-receipt-v1',mode=mode,query=query,snapshot=snapshot,
        status='disabled' if mode=='disabled' else 'backend_not_configured',hits=[],
        backend='none',executed_query=False,created_utc=now(),
        boundary='Empty placeholder, not evidence of retrieval effectiveness or filesystem/context isolation.')
    receipt['receipt_id']=evidence.digest(receipt)
    return receipt


def validate_retrieval(receipt):
    if (receipt.get('format')!='wannier-retrieval-receipt-v1' or
        evidence.digest({k:v for k,v in receipt.items() if k!='receipt_id'})!=receipt.get('receipt_id')):
        raise ValueError('Retrieval receipt identity mismatch.')
    # Until a real adapter is implemented, do not accept invented hit lists.
    expected='disabled' if receipt.get('mode')=='disabled' else 'backend_not_configured'
    if (receipt.get('mode') not in ('disabled','experience_assisted') or receipt.get('status')!=expected
        or receipt.get('hits')!=[] or receipt.get('backend')!='none' or receipt.get('executed_query') is not False):
        raise ValueError('This release supports only disabled/unconfigured empty retrieval receipts.')
    if receipt['mode']=='experience_assisted' and not receipt.get('snapshot'):
        raise ValueError('Missing knowledge snapshot.')


def validate_context(context, role, receipt):
    required=('experiment_id','task','evaluation_mode','actor','guidance')
    if not isinstance(context,dict) or any(k not in context for k in required):
        raise ValueError('Context requires experiment_id, task, evaluation_mode, actor and guidance.')
    if any(not isinstance(context[k],str) or not context[k].strip() for k in ('experiment_id','task')):
        raise ValueError('Experiment/task must be nonempty strings.')
    if context['evaluation_mode'] not in ('blind','development_replay','experience_assisted'):
        raise ValueError('Unknown evaluation mode.')
    actor=context['actor']
    if not isinstance(actor,dict) or actor.get('kind') not in ('ai','human','hybrid') or not actor.get('id'):
        raise ValueError('Record the decision actor kind and identifier.')
    if actor['kind'] in ('ai','hybrid') and (not actor.get('model') or not actor.get('prompt_id')):
        raise ValueError('AI context must identify its model and prompt version/hash.')
    if not isinstance(context['guidance'],list): raise ValueError('guidance must be a provenance list.')
    for item in context['guidance']:
        if (not isinstance(item,dict) or item.get('origin') not in
            ('task','human','general_rule','case_experience','previous_result') or not item.get('description')):
            raise ValueError('Each guidance item needs a known origin and description.')
    validate_retrieval(receipt)
    forbidden=role=='initial' or context['evaluation_mode']=='blind'
    if forbidden and (receipt['mode']!='disabled' or any(
        x['origin']=='case_experience' for x in context['guidance'])):
        raise ValueError('Initial and blind stages cannot consume case experience.')
    if role!='retry' and any(x['origin']=='previous_result' for x in context['guidance']):
        raise ValueError('Previous calculation results belong to retry or declared case experience, not initial/real_first_try.')
    if receipt['mode']=='experience_assisted' and context['evaluation_mode']!='experience_assisted':
        raise ValueError('Retrieval access disagrees with the evaluation mode.')


def physics(decision):
    """Comparable choices, including the reviewed exclusion rationale."""
    w=decision['windows']
    result=dict(basis=decision['trial_basis'],band_selection=decision.get('band_selection'),
        dis_eV=w['dis_eV'],frozen_eV=w['frozen_eV'],
        target=w['reference_domain'].get('complete_band_ranges_1based'),status=decision['status'])
    if 'trial_intent' in decision: result['trial_intent']=decision['trial_intent']
    for name in ('task_scope_review','additional_basis_evidence'):
        if name in decision: result[name]=decision[name]
    return result


def verify(stage_dir, seen=None):
    directory=Path(stage_dir).resolve();path=directory/'STAGE.json';record=read(path)
    seen=set() if seen is None else seen
    if str(directory) in seen: raise ValueError('Cyclic decision-stage ancestry.')
    seen.add(str(directory))
    if record.get('format')!=FORMAT or record.get('role') not in ROLES:
        raise ValueError('Unknown decision stage.')
    if evidence.digest({k:v for k,v in record.items() if k!='stage_id'})!=record.get('stage_id'):
        raise ValueError('Stage manifest identity mismatch.')
    if set(record['files']) not in (set(LEGACY_FILES),set(FILES_V1),set(FILES)):
        raise ValueError('Incomplete stage snapshot inventory.')
    for name,digest in record['files'].items():
        p=directory/name
        if p.is_symlink() or not p.is_file() or sha(p)!=digest:
            raise ValueError('Stage artifact changed or missing: '+str(p))
    decision=read(directory/'decision.json')
    if decision.get('decision_stage')!=dict(role=record['role'],manifest='STAGE.json'):
        raise ValueError('Decision/stage role binding mismatch.')
    if physics(decision)!=record['choices']: raise ValueError('Stage choices disagree with decision.')
    context=read(directory/'context.json');receipt=read(directory/'retrieval.json')
    validate_context(context,record['role'],receipt)
    for name,item in record['inputs'].items():
        if sha(item['path'])!=item['sha256']: raise ValueError('Stage input changed: '+name)
    parent=record.get('parent')
    if record['role']=='initial':
        if parent is not None or record['scientific_trial_index'] is not None:
            raise ValueError('Initial cannot have a trial index or parent.')
    else:
        if not parent: raise ValueError('A selected stage needs a parent.')
        old=verify(directory/parent['relative_path'],seen)
        if old['stage_id']!=parent['stage_id']: raise ValueError('Parent stage changed.')
        expected='real_first_try' if old['role']=='initial' else 'retry'
        if record['role']!=expected: raise ValueError('Invalid stage transition.')
        if record['scientific_trial_index']!=(1 if expected=='real_first_try' else old['scientific_trial_index']+1):
            raise ValueError('Scientific trial index is not consecutive.')
        parent_context=read((directory/parent['relative_path'])/'context.json')
        if (record['task']!=old['task'] or record['inputs']!=old['inputs'] or
                context['experiment_id']!=parent_context['experiment_id']):
            raise ValueError('Experiment/task/DFT changed; create a separate case/initial baseline.')
        if record['role']=='retry':
            result=verify_result(directory/record['parent_result']['relative_path'])
            if sha(directory/record['parent_result']['relative_path'])!=record['parent_result']['sha256'] or result['stage_id']!=old['stage_id']:
                raise ValueError('Retry must reference an unchanged result of its parent.')
    return record


def require_executable_decision(decision_path, cache=None):
    path=Path(decision_path).resolve();decision=read(path)
    tag=decision.get('decision_stage')
    if not tag or tag.get('manifest')!='STAGE.json':
        raise ValueError('Use decision_stages initial/select before new execution; legacy unstaged decisions remain read-only.')
    record=verify(path.parent)
    if path.name!='decision.json' or record['role']=='initial':
        raise ValueError('Initial is a non-executable baseline; explicitly select a real_first_try first.')
    if decision['status']!='proposed_trial': raise ValueError('Selected proposal has not passed the numerical gates.')
    if cache is not None and sha(cache)!=record['inputs']['cache']['sha256']:
        raise ValueError('Execution cache differs from the selected stage.')
    evidence.check_record(decision,read(path.parent/'analysis.json'),record['inputs']['cache']['path'])
    return dict(stage_id=record['stage_id'],role=record['role'],trial_id=record['trial_id'],
        decision_path=str(path),decision_sha256=sha(path),cache=record['inputs']['cache'])


def require_execution_binding(binding):
    if not binding: raise ValueError('New execution requires a sealed real_first_try/retry stage; prepare through decision_stages.')
    current=require_executable_decision(binding['decision_path'],binding['cache']['path'])
    if current!=binding: raise ValueError('Execution binding differs from the selected stage.')
    return current


def snapshot(case_dir, review_path, context_path, parent=None, trial_id=None,
             reason=None, retrieval_path=None, parent_result=None, baseline_control=False):
    start=time.monotonic();started=now();case=layout.load_case(case_dir)
    cache=case/'02_band/charge.npz';band_path=case/'02_band/fit_path.json'
    inputs={k:dict(path=str(p.absolute()),sha256=sha(p)) for k,p in [('cache',cache),('band_path',band_path)]}
    context=read(context_path);review=read(review_path)
    receipt=read(retrieval_path) if retrieval_path else retrieve('disabled',{})
    old=None;parent_dir=None;index=None
    if parent is None:
        # An initial snapshot may preserve an explicitly exploratory hypothesis.
        # It is still non-executable: role binding and explicit stage selection,
        # not asserted scientific certainty, control execution eligibility.
        role='initial';destination=layout.local_path(case,'03_wan_trial','initial')
        if trial_id or parent_result or baseline_control: raise ValueError('Initial is not a trial/control execution.')
    else:
        parent_dir=Path(parent).resolve();layout.inside(parent_dir,case/'03_wan_trial')
        old=verify(parent_dir);role='real_first_try' if old['role']=='initial' else 'retry'
        index=1 if role=='real_first_try' else old['scientific_trial_index']+1
        if trial_id!=f'trial_{index:03d}': raise ValueError(f'Use trial_{index:03d}; initial does not consume a trial number.')
        if not isinstance(reason,str) or not reason.strip(): raise ValueError('Explain changes or why the initial is retained.')
        parent_context=read(parent_dir/'context.json')
        if (inputs!=old['inputs'] or context['task']!=old['task'] or
                context['experiment_id']!=parent_context['experiment_id']):
            raise ValueError('New experiment/task/DFT requires a new case and initial.')
        trial=layout.trial_path(case,trial_id)
        destination=layout.local_path(case,'03_wan_trial',trial_id,'records','selection')
        if any((trial/d).exists() for d in ('interface','wannier','comparison')):
            raise ValueError('Cannot select over an already prepared/executed trial.')
        if role=='retry':
            if not parent_result: raise ValueError('A retry requires its parent result snapshot, including failed runs.')
            result=verify_result(parent_result)
            if result.get('stage_id')!=old['stage_id']: raise ValueError('Result belongs to another parent.')
        elif parent_result: raise ValueError('real_first_try does not follow a calculation result.')
    if destination.exists(): raise ValueError('Stage already sealed; snapshots are never overwritten.')
    validate_context(context,role,receipt)
    inspection=selection.inspect(cache)
    proposal,analysis,decision,validation=selection.propose(cache,inspection,review,str(destination/'review.json'))
    conduction_report=(conduction.questions(cache,proposal) if role=='initial'
                       else conduction.verify(cache,proposal,review))
    choices=physics(decision)
    changes={} if old is None else {k:dict(before=old['choices'].get(k),after=choices.get(k))
        for k in sorted(set(choices)|set(old['choices'])) if old['choices'].get(k)!=choices.get(k)}
    if old and decision['status']!='proposed_trial':
        raise ValueError('Selected stage is not numerically ready; inspect/refine with manifold_selection before select.')
    if baseline_control and (role!='real_first_try' or changes or receipt['mode']!='disabled'):
        raise ValueError('A baseline control must explicitly select an unchanged initial without retrieval.')
    if old and not trial.exists(): layout.new_trial(case,trial_id)
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix='.stage-',dir=destination.parent))
    try:
        decision['decision_stage']=dict(role=role,manifest='STAGE.json')
        validation['interface_numeric_precheck_pass']=validation.get('interface_preparation_allowed',False)
        validation['requires_explicit_stage_selection']=role=='initial'
        validation['interface_preparation_allowed']=role!='initial' and validation['interface_numeric_precheck_pass']
        if role=='initial' and 'trial_intent' in review:
            validation['initial_intent_role']='Non-executable exploratory hypothesis metadata; explicitly select a real_first_try and pass every executable-stage gate.'
        for name,data in [('inspection',inspection),('review',review),('analysis',analysis),('decision',decision),
                          ('selection',proposal),('validation',validation),('context',context),('retrieval',receipt)]:
            save(temporary/(name+'.json'),data)
        selection.plot(cache,band_path,proposal,temporary)
        diagnostic=diagnostics.audit(cache,inspection,review,proposal)
        save(temporary/'review_diagnostics.json',diagnostic)
        (temporary/'review_diagnostics.md').write_text(diagnostics.markdown(diagnostic))
        save(temporary/'conduction_review.json',conduction_report)
        record=dict(format=FORMAT,role=role,trial_id=trial_id,scientific_trial_index=index,
            task=context['task'],choices=choices,changes=changes,reason=reason or 'Unexecuted task-manifold hypothesis before explicit trial selection.',
            baseline_control=baseline_control,inputs=inputs,
            parent=None if old is None else dict(relative_path=os.path.relpath(parent_dir,destination),stage_id=old['stage_id']),
            parent_result=None if not parent_result else dict(relative_path=os.path.relpath(Path(parent_result).resolve(),destination),sha256=sha(parent_result)),
            files={name:sha(temporary/name) for name in FILES},
            code_sha256={p.name:sha(p) for p in [Path(__file__),Path(selection.__file__),
                                                Path(selection.basis_scope.__file__),
                                                Path(evidence.__file__).with_name('orbital_channels.py'),
                                                Path(evidence.__file__),Path(diagnostics.__file__),
                                                Path(conduction.__file__)]},
            started_utc=started,finished_utc=now(),tool_wall_seconds=time.monotonic()-start,
            external_ai_cost=context.get('external_ai_cost'),
            token_note='External runner/accounting evidence only; unavailable is null, never fabricated zero.',
            executed_dft=False,executed_wannier=False,fit_accepted=False,
            scope='Immutable decision provenance and plots; neither scientific acceptance nor environment isolation.')
        record['stage_id']=evidence.digest(record);save(temporary/'STAGE.json',record)
        os.rename(temporary,destination)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)
    verify(destination);gallery_result=render(case)
    return dict(stage_id=record['stage_id'],role=role,directory=str(destination),
        decision=str(destination/'decision.json'),analysis=str(destination/'analysis.json'),
        changes=changes,execution_selected=role!='initial',executed_wannier=False,
        gallery=gallery_result['gallery'],gallery_warning=gallery_result['gallery_warning'])


def verify_result(path):
    record=read(path)
    if (record.get('format')!='wannier-result-snapshot-v1' or
        evidence.digest({k:v for k,v in record.items() if k!='result_id'})!=record.get('result_id')):
        raise ValueError('Result snapshot identity mismatch.')
    for item in record['artifacts'].values():
        if sha(item['path'])!=item['sha256']: raise ValueError('Result artifact changed: '+item['path'])
    return record


def technical_retry(case_dir, trial_id, reason):
    case=layout.load_case(case_dir);trial=layout.trial_path(case,trial_id)
    binding=require_executable_decision(trial/'records/selection/decision.json')
    if not reason or not reason.strip(): raise ValueError('A technical retry needs a concrete reason.')
    if not any((trial/p).exists() for p in ('interface','wannier')):
        raise ValueError('No prior prepared/executed calculation to restart.')
    root=layout.local_path(case,'03_wan_trial',trial_id,'technical');root.mkdir(exist_ok=True)
    indices=[int(p.name.split('_')[-1]) for p in root.glob('restart_*') if p.is_dir()]
    destination=root/f'restart_{max(indices,default=0)+1:03d}';destination.mkdir()
    save(destination/'RESTART.json',dict(format='wannier-technical-restart-v1',decision_stage=binding,
        reason=reason,created_utc=now(),scientific_proposal_changed=False,executed=False))
    return dict(directory=str(destination),interface_dir=str(destination/'interface'),
        wannier_dir=str(destination/'wannier'),comparison_dir=str(destination/'comparison'),
        scientific_trial_index=verify(trial/'records/selection')['scientific_trial_index'],executed=False)


def record_result(case_dir, trial_id, assessment_path):
    case=layout.load_case(case_dir);trial=layout.trial_path(case,trial_id)
    stage=verify(trial/'records/selection');assessment=read(assessment_path)
    if not all(k in assessment for k in ('observations','hypotheses','next_action')):
        raise ValueError('Assessment must separate observations, hypotheses and next_action.')
    artifacts={};runtime={}
    roots=[trial]+sorted((trial/'technical').glob('restart_*'))
    for root in roots:
      for relative in ('interface/interface_run.json','wannier/wannier_run.json','comparison/fit_review.json',
                       'comparison/fit_review.png','comparison/fit_review.pdf'):
        p=root/relative
        if p.is_file(): artifacts[str(p.relative_to(trial))]=dict(path=str(p),sha256=sha(p))
      for folder,filename in [('interface','trial_manifest.json'),('wannier','wannier_manifest.json')]:
        p=root/folder/filename
        run_path=root/folder/('interface_run.json' if folder=='interface' else 'wannier_run.json')
        if run_path.exists():
            manifest=read(p);binding=manifest.get('decision_stage')
            require_execution_binding(binding)
            if binding['stage_id']!=stage['stage_id']: raise ValueError('Run belongs to a different selection.')
            if evidence.digest({k:v for k,v in manifest.items() if k!='manifest_id'})!=manifest.get('manifest_id'):
                raise ValueError('Run manifest identity mismatch.')
            r=read(run_path)
            if r.get('manifest_id')!=manifest['manifest_id']: raise ValueError('Run record/manifest mismatch.')
            artifacts[str(p.relative_to(trial))]=dict(path=str(p),sha256=sha(p))
            for name,digest in r.get('output_hashes',{}).items():
                output=layout.local_path(root,folder,name)
                if sha(output)!=digest: raise ValueError('Execution output changed: '+str(output))
                artifacts[str(output.relative_to(trial))]=dict(path=str(output),sha256=digest)
            runtime[str(run_path.relative_to(trial))]={k:r.get(k) for k in ('status','returncode','wall_seconds','disentanglement')}
    if not runtime:
        raise ValueError('A result needs an actual interface or Wannier run record, including failures.')
    result_dir=layout.local_path(case,'03_wan_trial',trial_id,'records','results');result_dir.mkdir(exist_ok=True)
    name=f'result_{len(list(result_dir.glob("result_*.json")))+1:03d}.json'
    record=dict(format='wannier-result-snapshot-v1',stage_id=stage['stage_id'],trial_id=trial_id,
        recorded_utc=now(),artifacts=artifacts,runtime=runtime,assessment=assessment,
        assessment_source=dict(path=str(Path(assessment_path).resolve()),sha256=sha(assessment_path)),
        knowledge_status='candidate_not_promoted',fit_accepted=False,
        scope='Captured run records and externally attributed assessment; no automatic success certification or knowledge insertion.')
    record['result_id']=evidence.digest(record);save(result_dir/name,record);gallery_result=render(case)
    return dict(result=str(result_dir/name),result_id=record['result_id'],knowledge_status=record['knowledge_status'],
                gallery=gallery_result['gallery'],gallery_warning=gallery_result['gallery_warning'])


def stage_paths(case):
    root=case/'03_wan_trial'
    first=root/'initial'
    return ([first] if (first/'STAGE.json').is_file() else [])+sorted(root.glob('trial_*/records/selection'))


def export_case(case_dir, output):
    case=layout.load_case(case_dir);stages=[dict(path=str(p),record=verify(p)) for p in stage_paths(case)]
    results=[dict(path=str(p),sha256=sha(p),record=verify_result(p)) for p in sorted((case/'03_wan_trial').glob('trial_*/records/results/result_*.json'))]
    record=dict(format='wannier-case-trajectory-v1',case_dir=str(case),created_utc=now(),stages=stages,results=results,
        execution_records=list({v['path']:v for result in results for k,v in result['record']['artifacts'].items()
            if k.endswith(('/interface_run.json','/wannier_run.json'))}.values()),
        knowledge_status='candidate_not_promoted',vector_index_written=False,
        scope='Structured trajectory export. Plans, scientific trials, executions and technical restarts remain distinct; large artifacts are referenced, not embedded.')
    save(output,record);return dict(output=str(output),stages=len(stages),results=len(results),vector_index_written=False)


def render(case_dir):
    case=layout.load_case(case_dir);root=case/'03_wan_trial';rows=[]
    for p in stage_paths(case):
        s=verify(p);relative=os.path.relpath(p,root);label=s['role']+' / '+(s['trial_id'] or 'not submitted')
        choices=s['choices'];excluded=(choices.get('band_selection') or {}).get('exclude_bands_1based',[])
        summary=dict(basis=choices['basis'],dis_eV=choices['dis_eV'],frozen_eV=choices['frozen_eV'],
            excluded_bands=excluded,status=choices['status'],changes=s['changes'])
        rows.append('<section><h2>'+html.escape(label)+'</h2><p>'+html.escape(s['reason'])+'</p><pre>'+
            html.escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre><details><summary>Full reviewed choices</summary><pre>'+
            html.escape(json.dumps(choices,ensure_ascii=False,indent=2))+'</pre></details><a href="'+relative+'/STAGE.json">Stage record</a> · '+
            '<a href="'+relative+'/decision.json">Decision</a><br><img src="'+relative+'/selection.png" alt="Selection only">')
        if s['trial_id']:
            trial=root/s['trial_id']
            results=sorted((trial/'records/results').glob('result_*.json'))
            if results:
                rows.append('<p>Results: '+' · '.join('<a href="'+str(r.relative_to(root))+'">'+r.name+'</a>' for r in results)+'</p>')
            for r in sorted((trial/'technical').glob('restart_*/RESTART.json')):
                rows.append('<p>Technical restart: <a href="'+str(r.relative_to(root))+'">'+r.parent.name+'</a></p>')
            fits=[trial/'comparison/fit_review.png']+sorted((trial/'technical').glob('restart_*/comparison/fit_review.png'))
            for fit in fits:
                if fit.is_file(): rows.append('<img src="'+str(fit.relative_to(root))+'" alt="Fit review">')
            if not results: rows.append('<p>No recorded execution outcome for this proposal yet.</p>')
        rows.append('</section>')
    marker='<!-- managed: decision_stages -->'
    index=root/'index.html'
    if index.is_symlink() or (index.exists() and not index.read_text().startswith(marker)):
        raise ValueError('Refuse replacing an unmanaged decision index.')
    text=marker+'<!doctype html><meta charset="utf-8"><title>Wannier decision trajectory</title><style>body{max-width:1200px;margin:32px auto;font-family:system-ui}section{padding:20px;border-top:1px solid #ccc}img{max-width:100%}pre{white-space:pre-wrap}</style><h1>Initial → real first try → retries</h1><p>Selection plots are not fit acceptance. Initial is never submitted directly.</p>'+''.join(rows)
    (root/'index.html').write_text(text)
    layout.write_readme(case/'README.md',layout.case_readme(case))
    # The per-case trajectory is always produced. Refresh the cross-case view
    # for this experiment by default, while reporting a gallery failure rather
    # than misreporting the already sealed scientific stage as failed.
    gallery_result=None;gallery_warning=None
    initial_context=root/'initial/context.json'
    if initial_context.is_file():
        try:
            gallery_result=gallery.render(case.parent,experiment_id=read(initial_context)['experiment_id'])
        except (ValueError,KeyError,TypeError,OSError,json.JSONDecodeError) as error:
            gallery_warning=str(error)
    return dict(index=str(root/'index.html'),gallery=gallery_result,gallery_warning=gallery_warning)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for name in ('initial','select'):
        q=sub.add_parser(name);q.add_argument('--case-dir',required=True);q.add_argument('--review',required=True)
        q.add_argument('--context',required=True);q.add_argument('--retrieval')
        if name=='select':
            q.add_argument('--parent',required=True);q.add_argument('--trial-id',required=True);q.add_argument('--reason',required=True)
            q.add_argument('--parent-result');q.add_argument('--baseline-control',action='store_true')
    q=sub.add_parser('retrieve');q.add_argument('--mode',choices=['disabled','experience_assisted'],default='disabled');q.add_argument('--query',required=True);q.add_argument('--snapshot');q.add_argument('--out',required=True)
    q=sub.add_parser('record-result');q.add_argument('--case-dir',required=True);q.add_argument('--trial-id',required=True);q.add_argument('--assessment',required=True)
    q=sub.add_parser('technical-retry');q.add_argument('--case-dir',required=True);q.add_argument('--trial-id',required=True);q.add_argument('--reason',required=True)
    q=sub.add_parser('export-case');q.add_argument('--case-dir',required=True);q.add_argument('--out',required=True)
    q=sub.add_parser('verify');q.add_argument('--directory',required=True)
    q=sub.add_parser('render');q.add_argument('--case-dir',required=True)
    a=p.parse_args()
    try:
        if a.command=='initial': r=snapshot(a.case_dir,a.review,a.context,retrieval_path=a.retrieval)
        elif a.command=='select': r=snapshot(a.case_dir,a.review,a.context,a.parent,a.trial_id,a.reason,a.retrieval,a.parent_result,a.baseline_control)
        elif a.command=='retrieve': r=retrieve(a.mode,read(a.query),a.snapshot);save(a.out,r)
        elif a.command=='record-result': r=record_result(a.case_dir,a.trial_id,a.assessment)
        elif a.command=='technical-retry': r=technical_retry(a.case_dir,a.trial_id,a.reason)
        elif a.command=='export-case': r=export_case(a.case_dir,a.out)
        elif a.command=='verify': r=verify(a.directory)
        else: r=render(a.case_dir)
        print(json.dumps(r,ensure_ascii=False,allow_nan=False,indent=2))
    except (ValueError,KeyError,TypeError,OSError) as error: p.exit(2,str(error)+'\n')


if __name__=='__main__': main()
