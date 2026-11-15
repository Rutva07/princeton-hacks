"""Fail-closed Docker executor for generated scientific experiments."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import mimetypes
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .experiment_models import CodeBundle, ExecutionResult, ExperimentPlan, ValidationResult

ALLOWED_IMPORT_ROOTS = {
    'argparse', 'json', 'platform', 'time', 'pathlib', 'math', 'statistics',
    'csv', 'warnings', 'typing', 'dataclasses', 'collections', 'itertools',
    'functools', 'datetime', 'numpy', 'pandas', 'scipy', 'sklearn',
    'matplotlib', 'torch',
}
ALLOWED_REQUIREMENTS = {'numpy', 'pandas', 'scipy', 'scikit-learn', 'matplotlib', 'torch'}
DENIED_CALLS = {'eval', 'exec', 'compile', '__import__', 'input', 'breakpoint'}
DENIED_ATTRIBUTES = {
    'system', 'popen', 'spawn', 'fork', 'execv', 'execve', 'remove',
    'unlink', 'rmdir', 'rmtree', 'walk', 'getenv', 'environ',
}
EXPERIMENT_ID = re.compile(r'^exp_[a-f0-9]{12}$')


def experiment_root(experiment_id: str) -> Path:
    if not EXPERIMENT_ID.fullmatch(experiment_id):
        raise ValueError('Invalid server experiment ID')
    root = (settings.data_dir / 'experiments' / experiment_id).resolve()
    expected = (settings.data_dir / 'experiments').resolve()
    if expected not in root.parents:
        raise ValueError('Experiment path escaped data root')
    return root


def _literal_string(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _has_cli_argument(tree: ast.AST, flag: str) -> bool:
    """Check for an argparse-style declaration; runtime execution remains authoritative."""
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'add_argument'
        and any(_literal_string(arg) == flag for arg in node.args)
        for node in ast.walk(tree)
    )


def _has_output_filename(tree: ast.AST, filename: str) -> bool:
    """Look for a filename literal, not a comment mentioning it."""
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value == filename or node.value.endswith('/' + filename))
        for node in ast.walk(tree)
    )


def validate_bundle(bundle: CodeBundle, plan: ExperimentPlan) -> ValidationResult:
    errors = []
    warnings = []
    digest = hashlib.sha256(bundle.experiment_py.encode('utf-8')).hexdigest()
    try:
        tree = ast.parse(bundle.experiment_py, filename='experiment.py')
    except SyntaxError as exc:
        return ValidationResult(
            valid=False,
            errors=[f'Syntax error at line {exc.lineno}: {exc.msg}'],
            code_hash=digest,
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split('.')[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    errors.append(f'Import is not allowed in the sandbox policy: {alias.name}')
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ''
            if node.level or name.split('.')[0] not in ALLOWED_IMPORT_ROOTS:
                errors.append(f'Import is not allowed in the sandbox policy: {"." * node.level + name}')

        if isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if isinstance(node.func, ast.Name) and call_name in DENIED_CALLS:
                errors.append(f'Dangerous dynamic call is denied: {call_name}')
            if isinstance(node.func, ast.Attribute) and call_name in DENIED_ATTRIBUTES:
                errors.append(f'Unsafe method call is denied: {call_name}')
        if isinstance(node, ast.Attribute) and node.attr.startswith('__'):
            errors.append(f'Dunder attribute access is denied: {node.attr}')

    for requirement in bundle.requirements:
        normalized = re.split(r'[<>=!~\[]', requirement.lower().strip(), maxsplit=1)[0].strip()
        if normalized not in ALLOWED_REQUIREMENTS:
            errors.append(f'Dependency is not in the prebuilt executor image: {requirement}')

    if plan.resource_budget.network_access:
        errors.append('Generated experiments may not request network access')
    if not _has_cli_argument(tree, '--config'):
        errors.append('Experiment must declare the --config argument using argparse')
    if not _has_cli_argument(tree, '--output-dir'):
        errors.append('Experiment must implement the --output-dir interface using argparse')
    if not _has_output_filename(tree, 'metrics.json'):
        errors.append('Experiment must write outputs/metrics.json')
    if not _has_output_filename(tree, 'run_metadata.json'):
        errors.append('Experiment must write outputs/run_metadata.json')

    # Static checks are advisory about behavior; only the actual isolated run
    # and artifact verification can establish that the files were produced.
    return ValidationResult(
        valid=not errors,
        errors=sorted(set(errors)),
        warnings=warnings,
        code_hash=digest,
    )


def repair_unused_imports(bundle: CodeBundle) -> CodeBundle:
    """Drop unused, denied top-level imports; leave referenced imports for rejection.

    This is a candidate repair, not authorization to execute. The caller must
    persist the changed source and send it through the complete validator again.
    """
    try:
        tree = ast.parse(bundle.experiment_py)
    except SyntaxError:
        return bundle
    # Reflective namespace lookups and deferred annotations can reference an
    # import without an ast.Name load. Be conservative about deleting those.
    if any(isinstance(node, ast.Call) and _call_name(node.func) in {'globals', 'locals', 'vars'}
           for node in ast.walk(tree)):
        return bundle
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            referenced.update(re.findall(r'\b[A-Za-z_]\w*\b', node.value))
    changed = False
    body = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            aliases = []
            for alias in node.names:
                root = (alias.name if isinstance(node, ast.Import) else node.module or '').split('.')[0]
                denied = root not in ALLOWED_IMPORT_ROOTS or bool(getattr(node, 'level', 0))
                # Future directives affect compilation even without a name use.
                bound = alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name)
                if denied and root != '__future__' and alias.name != '*' and bound not in referenced:
                    changed = True
                else:
                    aliases.append(alias)
            if not aliases:
                continue
            node.names = aliases
        body.append(node)
    if not changed:
        return bundle
    tree.body = body
    return bundle.model_copy(update={'experiment_py': ast.unparse(tree) + '\n'})


async def docker_status():
    try:
        process = await asyncio.create_subprocess_exec(
            'docker', 'info', '--format', '{{.ServerVersion}}',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=8)
        if process.returncode != 0:
            return {'available': False, 'detail': stderr.decode(errors='replace').strip()[:500]}
        image = await asyncio.create_subprocess_exec(
            'docker', 'image', 'inspect', settings.experiment_image,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, image_error = await asyncio.wait_for(image.communicate(), timeout=8)
        return {
            'available': image.returncode == 0,
            'daemon': True,
            'version': stdout.decode().strip(),
            'image': settings.experiment_image,
            'detail': None if image.returncode == 0 else (
                f'Executor image is missing: {settings.experiment_image}. '
                'Run scripts/build-executor.sh.'
            ),
        }
    except (FileNotFoundError, asyncio.TimeoutError) as exc:
        return {'available': False, 'detail': f'Docker is unavailable: {type(exc).__name__}'}


def prepare_workspace(experiment_id: str, attempt_no: int, bundle: CodeBundle):
    root = experiment_root(experiment_id)
    attempt = root / 'attempts' / str(attempt_no)
    source = attempt / 'source'
    output = attempt / 'outputs'
    source.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    (source / 'experiment.py').write_text(bundle.experiment_py, encoding='utf-8')
    (source / 'config.json').write_text(
        json.dumps(bundle.config, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    (source / 'requirements.txt').write_text('\n'.join(bundle.requirements) + '\n', encoding='utf-8')
    (source / 'README.md').write_text(bundle.readme, encoding='utf-8')
    for path in source.iterdir():
        path.chmod(0o644)
    output.chmod(0o777)
    return root, source, attempt, output


async def _docker_stop(name):
    try:
        process = await asyncio.create_subprocess_exec(
            'docker', 'stop', '--time', '2', name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
    except FileNotFoundError:
        pass


def _check_required_outputs(output: Path):
    """Reject false-positive success before reporting an experiment as completed."""
    try:
        metrics_path = output / 'metrics.json'
        metadata_path = output / 'run_metadata.json'
        if metrics_path.is_symlink() or metadata_path.is_symlink():
            return 'Required output files cannot be symlinks.'
        if not metrics_path.is_file():
            return 'Experiment exited successfully but did not create metrics.json.'
        if not metadata_path.is_file():
            return 'Experiment exited successfully but did not create run_metadata.json.'
        metrics = json.loads(metrics_path.read_text(encoding='utf-8'))
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        if not isinstance(metrics, dict) or not isinstance(metrics.get('verdict'), str) or not metrics['verdict'].strip():
            return 'metrics.json must contain a non-empty verdict string.'
        if not isinstance(metadata, dict) or metadata.get('completed') is not True:
            return 'run_metadata.json must contain completed=true.'
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        return f'Invalid required output JSON: {type(exc).__name__}: {exc}'
    return None


async def execute_bundle(experiment_id: str, attempt_no: int, bundle: CodeBundle,
                         plan: ExperimentPlan, cancel_check):
    status = await docker_status()
    if not status.get('available'):
        return ExecutionResult(status='unavailable', error=status.get('detail') or 'Docker executor unavailable')

    root, source, attempt, output = prepare_workspace(experiment_id, attempt_no, bundle)
    container_name = f'rf-{experiment_id[4:]}-{attempt_no}'
    budget = plan.resource_budget
    args = [
        'docker', 'run', '--rm', '--name', container_name,
        '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true',
        '--memory', f'{min(budget.memory_mb, settings.experiment_memory_mb)}m',
        '--cpus', str(min(budget.cpu_cores, settings.experiment_cpu_cores)),
        '--pids-limit', str(settings.experiment_pids_limit),
        '--user', '65532:65532', '--ulimit', 'nofile=256:256',
        '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
        '--env', 'HOME=/tmp', '--env', 'MPLCONFIGDIR=/tmp/matplotlib',
        '--mount', f'type=bind,src={source},dst=/experiment,readonly',
        '--mount', f'type=bind,src={output},dst=/outputs',
        settings.experiment_image,
        'python', '/experiment/experiment.py', '--config', '/experiment/config.json',
        '--output-dir', '/outputs',
    ]
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return ExecutionResult(status='unavailable', error=f'Could not start Docker: {exc}')

    communicate = asyncio.create_task(process.communicate())
    timeout = min(budget.runtime_seconds, settings.experiment_max_runtime)
    final_status = 'failed'
    error = None
    while not communicate.done():
        if cancel_check():
            await _docker_stop(container_name)
            final_status = 'cancelled'
            error = 'Cancellation requested by an authorized collaborator.'
            break
        if time.monotonic() - started > timeout:
            await _docker_stop(container_name)
            final_status = 'timed_out'
            error = f'Execution exceeded the {timeout}s time limit.'
            break
        await asyncio.sleep(.35)

    try:
        stdout, _ = await asyncio.wait_for(communicate, timeout=8)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        stdout = b''
        error = error or 'Container did not stop cleanly.'

    runtime = round(time.monotonic() - started, 3)
    completed_at = datetime.now(timezone.utc).isoformat()
    logs = stdout.decode(errors='replace').splitlines()[-500:]
    if final_status not in ('cancelled', 'timed_out'):
        final_status = 'completed' if process.returncode == 0 else 'failed'
        if process.returncode != 0:
            error = 'Experiment process returned a non-zero exit code.'
        else:
            error = _check_required_outputs(output)
            if error:
                final_status = 'failed'

    outputs = []
    total = 0
    limit = settings.experiment_disk_mb * 1024 * 1024
    for path in sorted(output.rglob('*')):
        if path.is_symlink():
            continue
        if path.is_file():
            size = path.stat().st_size
            total += size
            if total > limit:
                final_status = 'failed'
                error = f'Artifact output exceeded {settings.experiment_disk_mb} MB.'
                break
            outputs.append({
                'kind': 'output', 'name': path.name,
                'relative_path': str(path.relative_to(root)),
                'size_bytes': size,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'mime_type': mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
            })

    for path in sorted(source.iterdir()):
        if path.is_file():
            outputs.append({
                'kind': 'source', 'name': f'attempt-{attempt_no}-{path.name}',
                'relative_path': str(path.relative_to(root)),
                'size_bytes': path.stat().st_size,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'mime_type': mimetypes.guess_type(path.name)[0] or 'text/plain',
            })

    log_path = attempt / 'execution.log'
    log_path.write_text('\n'.join(logs) + ('\n' if logs else ''), encoding='utf-8')
    log_content = log_path.read_bytes()
    outputs.append({
        'kind': 'log', 'name': f'attempt-{attempt_no}-execution.log',
        'relative_path': str(log_path.relative_to(root)),
        'size_bytes': len(log_content),
        'sha256': hashlib.sha256(log_content).hexdigest(),
        'mime_type': 'text/plain',
    })
    return ExecutionResult(
        status=final_status, exit_code=process.returncode,
        runtime_seconds=runtime, logs=logs, error=error,
        outputs=outputs, container_name=container_name,
        executor_image=settings.experiment_image, command=args,
        started_at=started_at, completed_at=completed_at,
    )

