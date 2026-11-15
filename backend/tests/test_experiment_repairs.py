import asyncio
import json
import math
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app import storage
from app.executor import docker_status, execute_bundle, experiment_root, repair_unused_imports, validate_bundle
from app.experiment_graph import debugging_node, generation_node, validation_node
from app.experiment_models import CodeBundle, DatasetSpec, ExperimentPlan


REQUEST = ('Create and run y = sin(x) using 100 points between -pi and pi, '
           'a CPU PyTorch MLP with one hidden layer of 16 neurons for 100 epochs. '
           'Use random seed 42, save prediction.png and metrics.json, and report final MSE.')
SCRIPT = (Path(__file__).parent / 'fixtures' / 'sine_experiment.py').read_text()


def sine_plan():
    return ExperimentPlan(
        title='Tiny sine regression', objective=REQUEST, experiment_type='custom_python',
        dataset=DatasetSpec(name='Synthetic sine', source='torch.linspace and torch.sin'),
        methods=['MLP'], metrics=['final_mse'], random_seed=42, expected_training_runs=1,
        parameters={'data_points': 100, 'hidden_neurons': 16, 'epochs': 100},
        required_libraries=['torch', 'matplotlib'],
        expected_artifacts=['prediction.png', 'metrics.json', 'run_metadata.json'],
    )


def sine_bundle(script=SCRIPT):
    return CodeBundle(experiment_py=script, config=sine_plan().model_dump(),
                      requirements=['torch', 'matplotlib'], readme='CPU sine regression integration fixture.')


def experiment_state():
    storage.init_db()
    session = storage.create_session('Repair fixture')
    room = storage.create_room('Repair regression lab', session['participant']['id'])
    job = storage.create_experiment_job(room['id'], session['participant']['id'], REQUEST)
    return {'experiment_id': job['id'], 'room_id': room['id'],
            'user_id': session['participant']['id'], 'user_request': REQUEST,
            'experiment_plan': sine_plan().model_dump(), 'retry_count': 0, 'errors': []}


@pytest.mark.parametrize('language', ['python', 'py', '', 'Python'])
def test_single_fenced_file_is_normalized_before_validation(language):
    bundle = sine_bundle(f'```{language}\n{SCRIPT}\n```')
    assert bundle.experiment_py == SCRIPT + '\n'
    assert validate_bundle(bundle, sine_plan()).valid


@pytest.mark.parametrize('content', [
    f'Here is the code:\n```python\n{SCRIPT}\n```',
    f'```python\n{SCRIPT}\n```\nThis completes the experiment.',
    f'```python\n{SCRIPT}\n```\n```python\nprint("extra code")\n```',
])
def test_prose_and_multiple_files_still_fail_closed(content):
    assert not validate_bundle(sine_bundle(content), sine_plan()).valid


def test_local_import_repair_is_persisted_and_revalidated_without_model_call():
    state = experiment_state()
    bundle = sine_bundle('import traceback\nimport types, weakref\n' + SCRIPT)
    invalid = validate_bundle(bundle, sine_plan())
    assert not invalid.valid
    assert len(invalid.errors) == 3
    state.update(code_bundle=bundle.model_dump(), validation_result=invalid.model_dump())
    with patch('app.experiment_graph.get_model') as model:
        repaired = asyncio.run(debugging_node(state))
        model.assert_not_called()
    state.update(repaired)
    validated = asyncio.run(validation_node(state))
    assert validated['next_step'] == 'execute'
    detail = storage.get_experiment_detail(state['experiment_id'])
    assert any(item['name'] == 'unused_import_repair-1-experiment.py' for item in detail['artifacts'])
    repaired_code = state['code_bundle']['experiment_py']
    assert 'traceback' not in repaired_code
    assert 'weakref' not in repaired_code


@pytest.mark.parametrize('imports,usage', [
    ('import types as t', 'print(t.SimpleNamespace())'),
    ('from weakref import ref as r', 'print(r(main))'),
    ('import os', 'os.system("echo forbidden")'),
    ('from types import *', 'print("star import")'),
    ('import weakref', 'def deferred(value: "weakref.ReferenceType"):\n    return value'),
    ('import types', 'print(globals()["ty" + "pes"])'),
])
def test_referenced_or_star_imports_are_never_silently_removed(imports, usage):
    bundle = sine_bundle(imports + '\n' + SCRIPT + '\n' + usage)
    repaired = repair_unused_imports(bundle)
    assert repaired.experiment_py == bundle.experiment_py
    assert not validate_bundle(repaired, sine_plan()).valid


def test_invalid_syntax_and_dangerous_calls_are_not_repaired_into_execution():
    bundle = sine_bundle('import weakref\n' + SCRIPT + '\neval("1+1")\n')
    repaired = repair_unused_imports(bundle)
    assert not validate_bundle(repaired, sine_plan()).valid
    invalid = sine_bundle('This is invalid Python\n' + SCRIPT)
    assert repair_unused_imports(invalid) is invalid


def test_model_repair_normalizes_fences_and_receives_original_request_and_policy():
    state = experiment_state()
    # A used denied import needs a model repair rather than deletion.
    bundle = sine_bundle('import traceback\n' + SCRIPT + '\ntraceback.print_stack()\n')
    state.update(code_bundle=bundle.model_dump(), validation_result=validate_bundle(bundle, sine_plan()).model_dump())
    invoke = AsyncMock(return_value=sine_bundle(f'```python\n{SCRIPT}\n```'))

    class Model:
        def with_structured_output(self, schema):
            assert schema is CodeBundle
            return self
        ainvoke = invoke

    with patch('app.experiment_graph.model_available', return_value=True), \
         patch('app.experiment_graph.get_model', return_value=Model()):
        repaired = asyncio.run(debugging_node(state))
    prompt = invoke.call_args.args[0]
    assert REQUEST in prompt
    assert 'raw executable Python only' in prompt
    assert 'Import is not allowed in the sandbox policy: traceback' in prompt
    state.update(repaired)
    assert asyncio.run(validation_node(state))['next_step'] == 'execute'


def test_generation_also_receives_original_request_and_code_contract():
    state = experiment_state()
    invoke = AsyncMock(return_value=sine_bundle())

    class Model:
        def with_structured_output(self, schema):
            return self
        ainvoke = invoke

    with patch('app.experiment_graph.model_available', return_value=True), \
         patch('app.experiment_graph.get_model', return_value=Model()):
        generated = asyncio.run(generation_node(state))
    prompt = invoke.call_args.args[0]
    assert REQUEST in prompt
    assert 'raw executable Python only' in prompt
    assert validate_bundle(CodeBundle.model_validate(generated['code_bundle']), sine_plan()).valid


@pytest.mark.skipif(shutil.which('docker') is None, reason='Docker CLI is not installed')
def test_sine_experiment_runs_in_real_network_disabled_executor():
    status = asyncio.run(docker_status())
    if not status['available']:
        pytest.skip(status['detail'])
    state = experiment_state()
    bundle = sine_bundle()
    assert validate_bundle(bundle, sine_plan()).valid
    result = asyncio.run(execute_bundle(state['experiment_id'], 1, bundle, sine_plan(), lambda: False))
    assert result.status == 'completed', (result.error, result.logs)
    output = experiment_root(state['experiment_id']) / 'attempts' / '1' / 'outputs'
    metrics = json.loads((output / 'metrics.json').read_text())
    metadata = json.loads((output / 'run_metadata.json').read_text())
    assert math.isfinite(metrics['final_mse']) and 0 <= metrics['final_mse'] < 0.1
    assert metadata['data_points'] == 100
    assert metadata['epochs'] == 100
    assert metadata['hidden_neurons'] == 16
    assert metadata['random_seed'] == 42
    assert metadata['device'] == 'cpu'
    assert (output / 'prediction.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert result.command[result.command.index('--network') + 1] == 'none'
