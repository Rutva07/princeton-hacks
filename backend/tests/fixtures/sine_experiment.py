"""Handwritten CPU integration fixture for the generated-experiment interface."""
import argparse
import json
import math
import time
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import torch
from torch import nn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    torch.set_num_threads(1)
    torch.manual_seed(config['random_seed'])
    parameters = config['parameters']
    x = torch.linspace(-math.pi, math.pi, parameters['data_points']).reshape(-1, 1)
    y = torch.sin(x)
    model = nn.Sequential(nn.Linear(1, parameters['hidden_neurons']), nn.Tanh(), nn.Linear(parameters['hidden_neurons'], 1))
    optimizer = torch.optim.Adam(model.parameters(), lr=0.02)
    for _ in range(parameters['epochs']):
        optimizer.zero_grad()
        loss = nn.functional.mse_loss(model(x), y)
        loss.backward()
        optimizer.step()
    with torch.no_grad():
        predictions = model(x)
        mse = nn.functional.mse_loss(predictions, y).item()
    figure, axis = plt.subplots()
    axis.plot(x.numpy(), y.numpy(), label='True sin(x)')
    axis.plot(x.numpy(), predictions.numpy(), label='MLP prediction')
    axis.legend()
    figure.tight_layout()
    figure.savefig(output / 'prediction.png')
    plt.close(figure)
    metrics = {'final_mse': mse, 'verdict': f'Final training MSE after {parameters["epochs"]} epochs: {mse:.6f}.'}
    (output / 'metrics.json').write_text(json.dumps(metrics))
    metadata = {'completed': True, 'random_seed': config['random_seed'],
                'data_points': len(x), 'epochs': parameters['epochs'],
                'hidden_neurons': parameters['hidden_neurons'], 'training_runs': 1,
                'runtime_seconds': time.monotonic() - started,
                'device': str(next(model.parameters()).device)}
    (output / 'run_metadata.json').write_text(json.dumps(metadata))
    print(json.dumps(metrics))


if __name__ == '__main__':
    main()
