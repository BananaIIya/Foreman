"""Run one model-written routine component; receipts never claim correctness.

This file is uploaded to the task workspace. It contains no benchmark-specific
implementation. The component and its inputs must be supplied by task models.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def under(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Component files must stay in the task workspace')
    return path


def run_component(module_path, input_path, output_dir, receipt_path, purpose,
                  root='/app'):
    if purpose not in ('synthetic_check', 'original_task'):
        raise ValueError('Explicit invocation purpose required')
    module_path = under(root, module_path)
    input_path = under(root, input_path)
    output_dir = under(root, output_dir)
    receipt_path = under(root, receipt_path)
    if len({module_path, input_path, receipt_path}) != 3:
        raise ValueError('Module, input, and receipt must be distinct files')
    receipt = {'version': 1, 'invocation_id': uuid.uuid4().hex,
               'at': datetime.now(timezone.utc).isoformat(), 'purpose': purpose,
               'module_path': str(module_path), 'input_path': str(input_path),
               'output_dir': str(output_dir), 'status': 'failed',
               'python_executable': sys.executable,
               'model_calls': 0, 'correctness_proven': False}
    try:
        source = module_path.read_bytes()
        data = input_path.read_bytes()
        receipt['module_sha256'] = hashlib.sha256(source).hexdigest()
        receipt['input_sha256'] = hashlib.sha256(data).hexdigest()
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError('Component input must be a JSON object')
        # Compile the observed bytes: never reuse a stale Python bytecode cache.
        spec = importlib.util.spec_from_file_location('worker_component', module_path)
        if spec is None:
            raise ValueError('Component must be a Python module')
        module = importlib.util.module_from_spec(spec)
        previous = sys.modules.get('worker_component')
        sys.modules['worker_component'] = module
        try:
            exec(compile(source, str(module_path), 'exec'), module.__dict__)
            output_dir.mkdir(parents=True, exist_ok=True)
            result = module.run(payload, str(output_dir))
            if not isinstance(result, dict) or not isinstance(result.get('files'), list):
                raise ValueError('Component must return an object with a files array')
            files = []
            for name in result['files']:
                if not isinstance(name, str):
                    raise ValueError('Returned file paths must be strings')
                output = under(root, output_dir / name)
                if not output.is_relative_to(output_dir):
                    raise ValueError('Returned file is outside output_dir')
                if output in (module_path, input_path, receipt_path):
                    raise ValueError('Component cannot report its source or inputs as an output')
                content = output.read_bytes()
                files.append({'path': str(output), 'size_bytes': len(content),
                              'sha256': hashlib.sha256(content).hexdigest()})
            if not files:
                raise ValueError('Component returned no produced files')
            receipt.update(status='completed', files=files)
        finally:
            if previous is None:
                sys.modules.pop('worker_component', None)
            else:
                sys.modules['worker_component'] = previous
    except Exception as exc:
        receipt['error_type'] = type(exc).__name__
        raise
    finally:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        with receipt_path.open('a') as stream:
            stream.write(json.dumps(receipt, separators=(',', ':'))+'\n')
            stream.flush()
            os.fsync(stream.fileno())
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--module', default='/app/worker_component.py')
    parser.add_argument('--input', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--purpose', required=True, choices=['synthetic_check', 'original_task'])
    parser.add_argument('--receipt', default='/app/.worker-component-executions.jsonl')
    args = parser.parse_args()
    result = run_component(args.module, args.input, args.output_dir, args.receipt, args.purpose)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
