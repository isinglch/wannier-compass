#!/usr/bin/env python3
"""Record native provider usage with request deduplication and explicit pricing."""
import argparse
import hashlib
import json
from pathlib import Path

from workflow_runtime import atomic_json, now, sha, runtime_directory

FIELDS = ('input_uncached', 'cache_read', 'cache_write', 'output')


def count(value):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError('Native token counts must be nonnegative integers or null.')
    return value


def normalize(payload, provider):
    usage = payload.get('usage') or {}
    if provider == 'normalized':
        return {key: count(usage.get(key)) for key in FIELDS}
    if provider == 'deepseek':
        hit = count(usage.get('prompt_cache_hit_tokens'))
        miss = count(usage.get('prompt_cache_miss_tokens'))
        prompt = count(usage.get('prompt_tokens'))
        if miss is None and prompt is not None and hit is not None:
            miss = count(prompt - hit)
        if all(x is not None for x in (prompt, hit, miss)) and prompt != hit + miss:
            raise ValueError('Native DeepSeek prompt/cache counts disagree.')
        return dict(input_uncached=miss, cache_read=hit, cache_write=None,
                    output=count(usage.get('completion_tokens')))
    if provider == 'openai':
        prompt = count(usage.get('input_tokens', usage.get('prompt_tokens')))
        detail = usage.get('input_tokens_details', usage.get('prompt_tokens_details')) or {}
        hit = count(detail.get('cached_tokens'))
        return dict(input_uncached=count(prompt - hit) if prompt is not None and hit is not None else None,
                    cache_read=hit, cache_write=None,
                    output=count(usage.get('output_tokens', usage.get('completion_tokens'))))
    if provider == 'anthropic':
        return dict(input_uncached=count(usage.get('input_tokens')),
                    cache_read=count(usage.get('cache_read_input_tokens')),
                    cache_write=count(usage.get('cache_creation_input_tokens')),
                    output=count(usage.get('output_tokens')))
    raise ValueError('Use deepseek, openai, anthropic or explicitly normalized native usage.')


def record(workspace, source, provider, phase, request_id=None):
    if phase not in ('evidence', 'initial', 'real_first_try', 'retry', 'execution', 'assessment', 'engineering'):
        raise ValueError('Use one documented accounting phase.')
    source = Path(source).resolve()
    contents = source.read_bytes()
    payload = json.loads(contents)
    identity = payload.get('request_id') or payload.get('id') or request_id
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError('A native request/response identity is required for deduplication.')
    data = {'format': 'wannier-native-usage-v1', 'request_id': identity, 'provider': provider,
            'model': payload.get('model'), 'phase': phase,
            'usage': normalize(payload, provider),
            'source': str(source), 'source_sha256': hashlib.sha256(contents).hexdigest(),
            'caller_request_id': request_id}
    directory = runtime_directory(workspace, 'accounting', 'requests')
    path = directory / (hashlib.sha256((provider + ':' + identity).encode()).hexdigest() + '.json')
    duplicate = path.exists()
    if duplicate:
        existing = json.loads(path.read_text())
        if any(existing[k] != data[k] for k in ('provider', 'model', 'phase', 'usage')):
            raise ValueError('This native request is already recorded with different usage/model/phase.')
    else:
        data['recorded_utc'] = now()
        # Exclusive creation prevents a repeated export from adding a second charge.
        with path.open('x') as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
    return {'status': 'native_usage_recorded', 'request_id': identity, 'duplicate': duplicate,
            'record': str(path), 'sha256': sha(path), 'usage': data['usage']}


def report(workspace, pricing=None, out=None, provider=None, model=None, phase=None):
    directory = runtime_directory(workspace, 'accounting', 'requests')
    rows = [json.loads(p.read_text()) for p in sorted(directory.glob('*.json'))]
    rows = [r for r in rows if (provider is None or r['provider'] == provider)
            and (model is None or r['model'] == model) and (phase is None or r['phase'] == phase)]
    totals = {k: sum(r['usage'][k] for r in rows) if rows and all(r['usage'][k] is not None for r in rows) else None
              for k in FIELDS}
    result = {'format': 'wannier-native-usage-report-v1', 'requests': len(rows), 'usage': totals,
              'incomplete_fields': [k for k in FIELDS if totals[k] is None],
              'cost_components': None, 'total_cost': None, 'currency': None,
              'note': 'Token counts come from native usage; text length is never converted into tokens.'}
    if pricing:
        price_bytes = Path(pricing).read_bytes()
        prices = json.loads(price_bytes)
        for key in ('model', 'provider', 'currency', 'source', 'effective_date', 'rates_per_million', 'billable_fields'):
            if not prices.get(key):
                raise ValueError('Pricing requires explicit ' + key)
        if any(r['model'] != prices['model'] or r['provider'] != prices['provider'] for r in rows):
            raise ValueError('Price only matching native model/provider requests; split mixed-model reports.')
        fields = prices['billable_fields']
        if len(set(fields)) != len(fields) or any(k not in FIELDS for k in fields):
            raise ValueError('Invalid explicit billable_fields.')
        components = {}
        for key in fields:
            rate = prices['rates_per_million'].get(key)
            if type(rate) not in (float, int) or rate < 0:
                raise ValueError('Each billable field requires a nonnegative rate per million.')
            components[key] = totals[key] * rate / 1e6 if totals[key] is not None else None
        result.update(cost_components=components,
                      total_cost=sum(components.values()) if all(v is not None for v in components.values()) else None,
                      currency=prices['currency'], pricing={'path': str(Path(pricing).resolve()),
                            'sha256': hashlib.sha256(price_bytes).hexdigest(), **prices})
    target = Path(out) if out else directory.parent / 'report.json'
    atomic_json(target, result)
    return dict(result, report=str(target.resolve()), sha256=sha(target))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('record', 'report'))
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--source')
    parser.add_argument('--provider', choices=('deepseek', 'openai', 'anthropic', 'normalized'))
    parser.add_argument('--phase')
    parser.add_argument('--model')
    parser.add_argument('--request-id')
    parser.add_argument('--pricing')
    parser.add_argument('--out')
    args = parser.parse_args()
    try:
        result = (record(args.workspace, args.source, args.provider, args.phase, args.request_id)
                  if args.command == 'record' else report(args.workspace, args.pricing, args.out, args.provider, args.model, args.phase))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(2, 'Accounting error: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
