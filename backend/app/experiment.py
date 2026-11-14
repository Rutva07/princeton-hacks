"""Reproducible, bounded real PyTorch experiment. No arbitrary LLM code execution."""
import math
import random
import time
from pathlib import Path
import numpy as np
from sklearn.datasets import make_moons

# Import torch lazily to keep API responsive on boot.

def rbf_mmd2(x: np.ndarray, y: np.ndarray, sigma=0.6):
    def kernel(a,b):
        dist=((a[:,None,:]-b[None,:,:])**2).sum(axis=2)
        return np.exp(-dist/(2*sigma*sigma))
    return float(kernel(x,x).mean()+kernel(y,y).mean()-2*kernel(x,y).mean())

def run_traffic_experiment(demand: float = 4000.0, iterations: int = 80):
    """Solve a small Wardrop traffic assignment demonstrating Braess' paradox.

    The baseline has two symmetric routes. The candidate adds a zero-cost A→B
    connector. Method of successive averages assigns demand to shortest paths
    until link flows stabilize; no result is hard-coded.
    """
    routes_without={'north':('sa','at'),'south':('sb','bt')}
    routes_with={**routes_without,'connector':('sa','ab','bt')}

    def edge_cost(edge,flow):
        if edge in ('sa','bt'):return flow/100.0
        if edge in ('at','sb'):return 45.0
        return 0.0

    def solve(routes):
        edge_flows={edge:0.0 for path in routes.values() for edge in path}
        history=[]
        route_flows={name:0.0 for name in routes}
        for step in range(1,iterations+1):
            costs={name:sum(edge_cost(edge,edge_flows[edge]) for edge in path)
                   for name,path in routes.items()}
            best=min(costs.values())
            winners=[name for name,value in costs.items() if abs(value-best)<1e-9]
            target_routes={name:(demand/len(winners) if name in winners else 0.0) for name in routes}
            target_edges={edge:0.0 for edge in edge_flows}
            for name,path in routes.items():
                for edge in path:target_edges[edge]+=target_routes[name]
            alpha=1.0/step
            for edge in edge_flows:edge_flows[edge]+=(target_edges[edge]-edge_flows[edge])*alpha
            for name in route_flows:route_flows[name]+=(target_routes[name]-route_flows[name])*alpha
            if step in (1,2,5,10,20,40,iterations):
                history.append({'step':step,'average_travel_time':round(sum(
                    route_flows[name]*sum(edge_cost(edge,edge_flows[edge]) for edge in path)
                    for name,path in routes.items())/demand,4)})
        final_costs={name:sum(edge_cost(edge,edge_flows[edge]) for edge in path)
                     for name,path in routes.items()}
        average=sum(route_flows[name]*final_costs[name] for name in routes)/demand
        return {'average_travel_time':round(average,3),
                'route_flows':{k:round(v,1) for k,v in route_flows.items()},
                'route_costs':{k:round(v,3) for k,v in final_costs.items()},
                'edge_flows':{k:round(v,1) for k,v in edge_flows.items()},
                'convergence':history}

    baseline=solve(routes_without)
    candidate=solve(routes_with)
    delta=candidate['average_travel_time']-baseline['average_travel_time']
    return {'experiment':'Braess paradox — static traffic assignment',
      'experiment_type':'traffic_simulation','demand_vehicles':int(demand),
      'method':'Wardrop equilibrium approximation via method of successive averages',
      'iterations':iterations,'results':{'baseline':baseline,'candidate':candidate},
      'delta_average_travel_time':round(delta,3),
      'verdict':('Adding the connector increased equilibrium travel time in this network.' if delta>0 else
                 'The connector did not increase equilibrium travel time in this configuration.'),
      'limitations':['Stylized four-node network','Static demand','Deterministic route choice',
                     'No incidents or stochastic driver behavior','Illustrates a causal mechanism, not a city forecast']}


def run_experiment(max_seconds: int = 90, steps: int | None = None, seed: int = 17,
                   prompt: str = ''):
    """Train two 2D flow-matching MLPs; compare held-out FM MSE and sample MMD.

    Each recipe receives same initial weights, batch draws, val data, and optimizer
    step count. CPU time may differ; time budget bounds combined runtime.
    """
    if any(word in prompt.lower() for word in ('traffic','congestion','road','braess')):
        return run_traffic_experiment()
    import torch
    from torch import nn
    torch.set_num_threads(min(2, torch.get_num_threads()))
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    # Fixed toy data, standardized; not a scientific production benchmark.
    train_np,_=make_moons(n_samples=1600,noise=0.07,random_state=seed)
    val_np,_=make_moons(n_samples=256,noise=0.07,random_state=seed+1)
    train_np=((train_np-np.array([0.5,0.25]))/1.1).astype('float32')
    val_np=((val_np-np.array([0.5,0.25]))/1.1).astype('float32')
    train=torch.from_numpy(train_np)
    valid=torch.from_numpy(val_np)
    requested_steps = steps or min(260, max(50,max_seconds*4))
    # Benchmarked locally: cap at 300 to avoid runaway CPU usage.
    n_steps = min(300,max(30,requested_steps))
    torch.manual_seed(seed)
    def network():
        return nn.Sequential(nn.Linear(3,64),nn.SiLU(),nn.Linear(64,64),nn.SiLU(),nn.Linear(64,2))
    init=network().state_dict()
    batches=[]
    g=torch.Generator().manual_seed(seed+55)
    for _ in range(n_steps):
        idx=torch.randint(0,len(train),(128,),generator=g)
        x0=torch.randn(128,2,generator=g)
        t=torch.rand(128,1,generator=g)
        batches.append((x0,train[idx],t))
    start=time.monotonic()
    runs={}
    for key in ['baseline','candidate']:
        torch.manual_seed(seed)
        net=network()
        net.load_state_dict(init)
        net.train()
        opt=(torch.optim.Adam(net.parameters(),lr=0.003) if key=='baseline' else
             torch.optim.AdamW(net.parameters(),lr=0.003,weight_decay=0.001))
        scheduler=None if key=='baseline' else torch.optim.lr_scheduler.CosineAnnealingLR(opt,T_max=n_steps,eta_min=0.0003)
        losses=[]
        for i,(x0,x1,t) in enumerate(batches):
            xt=(1-t)*x0+t*x1
            target=x1-x0
            pred=net(torch.cat([xt,t],dim=1))
            loss=((pred-target)**2).mean()
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            if scheduler:scheduler.step()
            if i%10==0 or i==n_steps-1:losses.append({'step':i+1,'loss':round(float(loss.item()),6)})
            if time.monotonic()-start > max_seconds:
                # Stop early on both runs with a traceable failed budget rather than compare unequal steps.
                raise TimeoutError('Experiment exceeded time budget; increase budget or reduce steps')
        net.eval()
        with torch.no_grad():
            gv=torch.Generator().manual_seed(seed+222)
            x0=torch.randn(256,2,generator=gv)
            t=torch.rand(256,1,generator=gv)
            xt=(1-t)*x0+t*valid
            val_mse=float(((net(torch.cat([xt,t],dim=1))-(valid-x0))**2).mean().item())
            # Euler numerical sampling of learned dx/dt=v_theta(x,t)
            g_sample=torch.Generator().manual_seed(seed+333)
            generated=torch.randn(256,2,generator=g_sample)
            for j in range(32):
                tj=torch.full((256,1),j/32,dtype=torch.float32)
                generated=generated+(1/32)*net(torch.cat([generated,tj],dim=1))
            generated_np=generated.numpy()
        runs[key]={'validation_mse':round(val_mse,6),
                   'sample_mmd2':round(rbf_mmd2(generated_np,val_np),6),
                   'loss_history':losses,'generated_samples':generated_np[:120].round(4).tolist(),
                   'runtime_seconds':round(time.monotonic()-start,2)}
    delta=runs['candidate']['sample_mmd2']-runs['baseline']['sample_mmd2']
    verdict=('Candidate achieved lower toy-sample MMD² than baseline.' if delta<0 else
             'Candidate did not improve toy-sample MMD² over baseline.')
    return {'experiment':'2D flow matching with Gaussian-to-two-moons transport','experiment_type':'flow_matching','dataset':'synthetic two-moons',
      'model':'MLP 3→64→64→2, SiLU','seed':seed,'steps_per_recipe':n_steps,
      'matched_controls':['identical initialization','same sampled minibatches','same validation samples',
                          'equal optimizer steps','same architecture','same Euler sampling NFE=32'],
      'metrics':{'mmd2':'lower is better; biased RBF MMD² at sigma=0.6',
                 'validation_mse':'lower is better; held-out flow matching regression error'},
      'results':runs, 'delta_mmd2_candidate_minus_baseline':round(delta,6), 'verdict':verdict,
      'limitations':['One random seed','small toy dataset','small CPU model','not a paper reproduction',
                     'No statistical significance claim','No universal improvement claim'],
      'total_runtime_seconds':round(time.monotonic()-start,2),
      'reference_samples':val_np[:120].round(4).tolist()}
