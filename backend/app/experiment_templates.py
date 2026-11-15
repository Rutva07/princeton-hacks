"""Prevalidated scientific templates used before unrestricted model generation."""
from __future__ import annotations

import re

from .experiment_models import CodeBundle, DatasetSpec, ExperimentPlan, ResourceBudget


def supports_classification(request: str):
    text=request.lower()
    return any(term in text for term in (
        'classification','classifier','logistic regression','random forest',
        'gradient boosting','breast cancer','iris dataset','wine dataset'))


def supports_clt(request: str):
    """Only use the exponential CLT template when its assumptions are explicit."""
    text = request.lower()
    clt_topic = ('central limit theorem' in text or 'sample means' in text)
    exponential = bool(re.search(r'\b(exponential|exp\s*\(|exp\s*distribution)\b', text))
    incompatible = bool(re.search(r'\b(normal|gaussian|uniform|poisson|bernoulli|gamma|weibull)\b', text))
    return clt_topic and exponential and not incompatible


def _objective(request: str, fallback: str):
    """Keep the typed objective within its 2,000-character schema limit."""
    request = request.strip()
    return request if 8 <= len(request) <= 2000 else fallback


def _clt_sizes(request: str):
    """Recognize explicit sample-size lists; otherwise retain template defaults."""
    match = re.search(r'(?:sample\s+sizes?|n\s*(?:=|in))\s*(?:of|:|=|in)?\s*[\[{(]?\s*(\d+(?:\s*[,/]\s*\d+)+)', request, re.I)
    if not match:
        return [1, 5, 10, 30, 50, 100]
    values = [int(v) for v in re.findall(r'\d+', match.group(1))]
    if not (1 <= len(values) <= 6 and all(1 <= n <= 100000 for n in values)):
        raise ValueError('The exponential CLT template supports 1–6 sample sizes between 1 and 100000.')
    return list(dict.fromkeys(values))


def _clt_repetitions(request: str):
    patterns = (
        r'(\d[\d,]*)\s+(?:independent\s+)?sample means',
        r'(\d[\d,]*)\s+(?:repetitions|replicates|simulations|trials|runs)\b',
        r'(?:repeat|simulate|run)\s+(\d[\d,]*)\s+times',
    )
    for pattern in patterns:
        match = re.search(pattern, request, re.I)
        if match:
            count = int(match.group(1).replace(',', ''))
            if not 2 <= count <= 100000:
                raise ValueError('The exponential CLT template supports 2–100000 simulations per size.')
            return count
    return 10000


def _requested_seed_count(text: str):
    match=re.search(r'(\d+)\s+(?:different\s+|random\s+)?(?:random\s+)?seeds?',text,re.I)
    return min(100,max(1,int(match.group(1)))) if match else 1


def classification_plan(request: str,seed: int = 42,source_urls=None):
    text=request.lower();methods=[]
    if 'logistic' in text:methods.append('Logistic Regression')
    if 'random forest' in text:methods.append('Random Forest')
    if 'gradient boost' in text:methods.append('Gradient Boosting')
    if not methods:methods=['Logistic Regression','Random Forest']
    if len(methods)==1:methods.append('Random Forest' if methods[0]!='Random Forest' else 'Logistic Regression')
    if 'iris' in text:
        dataset=DatasetSpec(name='Iris',source='scikit-learn built-in datasets',version='sklearn bundled')
    elif 'wine' in text:
        dataset=DatasetSpec(name='Wine',source='scikit-learn built-in datasets',version='sklearn bundled')
    else:
        dataset=DatasetSpec(name='Breast Cancer Wisconsin Diagnostic',
            source='scikit-learn built-in datasets',version='sklearn bundled',
            description='569 diagnostic samples with 30 numeric features and binary labels.')
    count=_requested_seed_count(request);seeds=list(range(seed,seed+count))
    return ExperimentPlan(
        title=f"{' vs '.join(methods)} on {dataset.name}",
        objective=_objective(request, "Compare classification models on matched stratified splits."),
        experiment_type='classification_comparison',dataset=dataset,methods=methods,
        metrics=['accuracy','precision','recall','weighted_f1','roc_auc','95% confidence intervals','paired t-tests'],
        random_seed=seed,random_seeds=seeds,expected_training_runs=len(methods)*count,
        parameters={'template':'classification_repeated_splits','test_size':.2,'confidence_level':.95},
        preprocessing=['Matched stratified 80/20 splits','Training-only scaling for logistic regression'],
        split_strategy=f'{count} matched stratified 80/20 train/test splits using explicit seeds.',
        evaluation_procedure=('Fit every method on every identical split; aggregate held-out metrics, '
                              '95% confidence intervals, and paired tests across seeds.'),
        required_libraries=['numpy','pandas','scipy','scikit-learn','matplotlib'],
        expected_artifacts=['metrics.json','results.csv','comparison.png','roc_curves.png',
                            'confusion_matrices.png','run_metadata.json'],
        resource_budget=ResourceBudget(runtime_seconds=300 if count>10 else 120,memory_mb=1536,cpu_cores=1),
        reproduction_level='not_applicable',source_urls=source_urls or [],
        limitations=['Single bundled tabular dataset',f'{count} repeated holdout splits are statistically dependent',
                     'Model families receive modest fixed hyperparameters',
                     'Paired tests across repeated splits are descriptive, not definitive causal evidence'])


CLASSIFICATION_SCRIPT = r'''import argparse
import json
import platform
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.datasets import load_breast_cancer, load_iris, load_wine
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score, precision_score,
                             recall_score, roc_auc_score, roc_curve)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def load_dataset(name):
    if name == "Iris": return load_iris(return_X_y=True)
    if name == "Wine": return load_wine(return_X_y=True)
    return load_breast_cancer(return_X_y=True)


def make_model(name,seed):
    if name == "Logistic Regression":
        return make_pipeline(StandardScaler(),LogisticRegression(max_iter=1500,random_state=seed))
    if name == "Gradient Boosting":return GradientBoostingClassifier(random_state=seed,n_estimators=100)
    if name == "Random Forest":
        return RandomForestClassifier(n_estimators=160,random_state=seed,n_jobs=1,max_depth=None)
    raise ValueError(f"Unsupported verified method: {name}")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True);parser.add_argument("--output-dir",required=True)
    args=parser.parse_args();output=Path(args.output_dir);output.mkdir(parents=True,exist_ok=True)
    config=json.loads(Path(args.config).read_text());seeds=[int(v) for v in config["random_seeds"]]
    X,y=load_dataset(config["dataset"]["name"]);labels=np.unique(y);rows=[];last_roc={}
    matrices={method:np.zeros((len(labels),len(labels)),dtype=int) for method in config["methods"]}
    started=time.time()
    for seed in seeds:
        X_train,X_test,y_train,y_test=train_test_split(X,y,test_size=.2,random_state=seed,stratify=y)
        for method in config["methods"]:
            model=make_model(method,seed);model.fit(X_train,y_train);pred=model.predict(X_test)
            probability=model.predict_proba(X_test) if hasattr(model,"predict_proba") else None
            if probability is not None:
                score=probability[:,1] if probability.shape[1]==2 else probability
                area=float(roc_auc_score(y_test,score,multi_class="ovr" if probability.shape[1]>2 else "raise"))
                if probability.shape[1]==2:last_roc[method]=roc_curve(y_test,score)[:2]
            else:area=None
            matrices[method]+=confusion_matrix(y_test,pred,labels=labels)
            rows.append({"seed":seed,"method":method,"accuracy":float(accuracy_score(y_test,pred)),
                "precision":float(precision_score(y_test,pred,average="weighted",zero_division=0)),
                "recall":float(recall_score(y_test,pred,average="weighted",zero_division=0)),
                "weighted_f1":float(f1_score(y_test,pred,average="weighted")),"roc_auc":area,
                "train_samples":int(len(y_train)),"test_samples":int(len(y_test))})
    frame=pd.DataFrame(rows);frame.to_csv(output/"results.csv",index=False);summaries=[]
    for method,group in frame.groupby("method",sort=False):
        item={"method":method,"seed_count":int(len(group))}
        for metric in ("accuracy","precision","recall","weighted_f1","roc_auc"):
            values=group[metric].dropna().to_numpy(dtype=float);mean=float(values.mean())
            std=float(values.std(ddof=1)) if len(values)>1 else 0.0
            half=float(1.96*std/np.sqrt(len(values))) if len(values)>1 else 0.0
            item[metric]=mean;item[f"{metric}_std"]=std;item[f"{metric}_ci95"]=[mean-half,mean+half]
        summaries.append(item)
    summary=pd.DataFrame(summaries);best=summary.sort_values(["weighted_f1","accuracy"],ascending=False).iloc[0]
    paired=[]
    for index,left in enumerate(config["methods"]):
        for right in config["methods"][index+1:]:
            a=frame[frame.method==left].sort_values("seed")["weighted_f1"].to_numpy()
            b=frame[frame.method==right].sort_values("seed")["weighted_f1"].to_numpy()
            statistic,pvalue=stats.ttest_rel(a,b) if len(seeds)>1 else (float("nan"),float("nan"))
            paired.append({"metric":"weighted_f1","left":left,"right":right,
                "mean_difference":float((a-b).mean()),"t_statistic":None if np.isnan(statistic) else float(statistic),
                "p_value":None if np.isnan(pvalue) else float(pvalue)})
    metrics={"experiment_type":"classification_comparison","dataset":config["dataset"],
        "random_seed":seeds[0],"random_seeds":seeds,"seed_count":len(seeds),"training_runs":len(rows),
        "methods":summaries,"paired_tests":paired,"best_method":best["method"],
        "verdict":f"{best['method']} had the highest mean held-out weighted F1 across {len(seeds)} matched splits.",
        "limitations":config.get("limitations",[])}
    (output/"metrics.json").write_text(json.dumps(metrics,indent=2))
    positions=np.arange(len(summary));width=.35;fig,ax=plt.subplots(figsize=(9,4.8))
    ax.bar(positions-width/2,summary.accuracy,width,yerr=summary.accuracy_std,label="Accuracy",capsize=3,color="#2d7d63")
    ax.bar(positions+width/2,summary.weighted_f1,width,yerr=summary.weighted_f1_std,label="Weighted F1",capsize=3,color="#ed7a55")
    ax.set_xticks(positions,summary.method,rotation=10,ha="right");ax.set_ylim(0,1.05);ax.legend();ax.grid(axis="y",alpha=.2)
    fig.tight_layout();fig.savefig(output/"comparison.png",dpi=150);plt.close(fig)
    fig,ax=plt.subplots(figsize=(7,5))
    for method,(false_positive,true_positive) in last_roc.items():ax.plot(false_positive,true_positive,label=method)
    ax.plot([0,1],[0,1],"--",color="#999");ax.set_xlabel("False positive rate");ax.set_ylabel("True positive rate")
    if last_roc:ax.legend()
    ax.grid(alpha=.2);fig.tight_layout();fig.savefig(output/"roc_curves.png",dpi=150);plt.close(fig)
    fig,axes=plt.subplots(1,len(config["methods"]),figsize=(4*len(config["methods"]),3.8),squeeze=False)
    for ax,method in zip(axes[0],config["methods"]):
        matrix=matrices[method];ax.imshow(matrix,cmap="Greens")
        for (row,column),value in np.ndenumerate(matrix):ax.text(column,row,str(value),ha="center",va="center")
        ax.set_title(method);ax.set_xlabel("Predicted");ax.set_ylabel("Actual")
    fig.tight_layout();fig.savefig(output/"confusion_matrices.png",dpi=150);plt.close(fig)
    metadata={"python":platform.python_version(),"runtime_seconds":round(time.time()-started,3),
        "random_seeds":seeds,"seed_count":len(seeds),"model_count":len(config["methods"]),
        "training_runs":len(rows),"expected_training_runs":config["expected_training_runs"],
        "dataset_rows":int(len(y)),"dataset_features":int(X.shape[1]),"completed":True}
    (output/"run_metadata.json").write_text(json.dumps(metadata,indent=2))
    print(json.dumps({"event":"completed","best_method":best["method"],"seed_count":len(seeds),
                      "training_runs":len(rows),"progress":100}))


if __name__ == "__main__":main()
'''


def classification_bundle(plan: ExperimentPlan):
    return CodeBundle(experiment_py=CLASSIFICATION_SCRIPT,config=plan.model_dump(),
        requirements=['numpy','pandas','scipy','scikit-learn','matplotlib'],template='verified_classification_v2',
        readme=('Verified repeated-split classification template with explicit seeds, aggregate confidence '
                'intervals, paired tests, raw per-run metrics, plots, and exact training-run counts.'))


CLT_SCRIPT = r'''import argparse
import json
import platform
import time
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True);parser.add_argument("--output-dir",required=True)
    args=parser.parse_args();output=Path(args.output_dir);output.mkdir(parents=True,exist_ok=True)
    config=json.loads(Path(args.config).read_text());params=config["parameters"]
    sizes=[int(value) for value in params["sample_sizes"]];simulations=int(params["simulations_per_size"])
    seed=int(config["random_seed"]);rng=np.random.default_rng(seed);started=time.time();summary=[];raw=[]
    columns=min(3,len(sizes));rows_count=(len(sizes)+columns-1)//columns
    fig_hist,axes=plt.subplots(rows_count,columns,figsize=(4.5*columns,3.5*rows_count),squeeze=False)
    axes=axes.ravel();warnings.filterwarnings("ignore",category=RuntimeWarning)
    for index,n in enumerate(sizes):
        means=rng.exponential(scale=1.0,size=(simulations,n)).mean(axis=1);standardized=np.sqrt(n)*(means-1.0)
        ks=stats.kstest(standardized,"norm")
        summary.append({"sample_size":n,"simulations":simulations,"empirical_mean":float(means.mean()),
            "empirical_variance":float(means.var(ddof=1)),"theoretical_variance":1.0/n,
            "empirical_skewness":float(stats.skew(means,bias=False)),"theoretical_skewness":2.0/np.sqrt(n),
            "excess_kurtosis":float(stats.kurtosis(means,bias=False)),
            "ks_statistic":float(ks.statistic),"ks_p_value":float(ks.pvalue)})
        raw.append(pd.DataFrame({"sample_size":n,"simulation":np.arange(simulations),"sample_mean":means}))
        ax=axes[index];ax.hist(standardized,bins=55,density=True,alpha=.7,color="#4c9b7a")
        grid=np.linspace(-4,4,300);ax.plot(grid,stats.norm.pdf(grid),color="#d66f50",linewidth=1.7)
        ax.set_title(f"n={n}");ax.set_xlim(-4,6 if n<5 else 4);ax.grid(alpha=.15)
    for unused_ax in axes[len(sizes):]:unused_ax.set_visible(False)
    fig_hist.suptitle("Standardized exponential sample means vs N(0,1)")
    fig_hist.tight_layout();fig_hist.savefig(output/"histograms.png",dpi=150);plt.close(fig_hist)
    frame=pd.DataFrame(summary);frame.to_csv(output/"metrics_summary.csv",index=False)
    pd.concat(raw,ignore_index=True).to_csv(output/"raw_simulation_results.csv",index=False)
    fig,axes=plt.subplots(1,3,figsize=(13,3.8))
    axes[0].plot(frame.sample_size,frame.empirical_variance,"o-",label="empirical")
    axes[0].plot(frame.sample_size,frame.theoretical_variance,"--",label="1/n");axes[0].set_title("Variance");axes[0].legend()
    axes[1].plot(frame.sample_size,frame.empirical_skewness,"o-",label="empirical")
    axes[1].plot(frame.sample_size,frame.theoretical_skewness,"--",label="2/sqrt(n)");axes[1].set_title("Skewness");axes[1].legend()
    axes[2].plot(frame.sample_size,frame.ks_statistic,"o-");axes[2].set_title("KS statistic vs normal")
    for ax in axes:ax.set_xlabel("Sample size");ax.grid(alpha=.2)
    fig.tight_layout();fig.savefig(output/"convergence_plots.png",dpi=150);plt.close(fig)
    total=simulations*len(sizes)
    metrics={"experiment_type":"clt_monte_carlo","distribution":"Exponential(rate=1)",
        "random_seed":seed,"sample_sizes":sizes,"simulations_per_size":simulations,"total_simulations":total,
        "summary":summary,"verdict":"Standardized exponential sample means became less skewed and closer to normal as sample size increased.",
        "limitations":config.get("limitations",[])}
    (output/"metrics.json").write_text(json.dumps(metrics,indent=2))
    metadata={"python":platform.python_version(),"runtime_seconds":round(time.time()-started,3),
        "random_seed":seed,"sample_sizes":sizes,"sample_size_count":len(sizes),
        "simulations_per_size":simulations,"total_simulations":total,
        "expected_simulations":config["expected_simulations"],"completed":True}
    (output/"run_metadata.json").write_text(json.dumps(metadata,indent=2))
    print(json.dumps({"event":"completed","total_simulations":total,"progress":100}))


if __name__ == "__main__":main()
'''


def clt_plan(request: str,seed: int = 42,source_urls=None):
    sizes=_clt_sizes(request)
    simulations=_clt_repetitions(request)
    return ExperimentPlan(
        title='Central Limit Theorem Monte Carlo for Exponential Samples',
        objective=_objective(request, 'Evaluate convergence of exponential sample means toward a normal distribution.'),
        experiment_type='custom_python',dataset=DatasetSpec(name='Synthetic Exponential(rate=1)',
            source='NumPy random generator',version='PCG64 fixed seed',
            description='Independent exponentially distributed observations with mean and variance 1.'),
        methods=['Monte Carlo sample means','Standard-normal comparison'],
        metrics=['empirical_mean','empirical_variance','skewness','excess_kurtosis','ks_statistic'],
        random_seed=seed,expected_simulations=simulations*len(sizes),
        parameters={'template':'clt_exponential','sample_sizes':sizes,'simulations_per_size':simulations,'rate_lambda':1.0},
        preprocessing=['Standardize each sample mean as sqrt(n) * (mean - 1)'],
        split_strategy='No train/test split; independent Monte Carlo batches per sample size.',
        evaluation_procedure=('Generate the exact requested number of sample means for every size; compare '
                              'empirical variance/skewness with theory and standardized means with N(0,1).'),
        required_libraries=['numpy','pandas','scipy','matplotlib'],
        expected_artifacts=['metrics.json','metrics_summary.csv','raw_simulation_results.csv',
                            'histograms.png','convergence_plots.png','run_metadata.json'],
        resource_budget=ResourceBudget(runtime_seconds=180,memory_mb=1536,cpu_cores=1),
        reproduction_level='not_applicable',source_urls=source_urls or [],
        limitations=['Finite Monte Carlo sample','One skewed source distribution','KS p-values depend on simulation count'])


def clt_bundle(plan: ExperimentPlan):
    return CodeBundle(experiment_py=CLT_SCRIPT,config=plan.model_dump(),
        requirements=['numpy','pandas','scipy','matplotlib'],template='verified_clt_exponential_v1',
        readme=('Verified Central Limit Theorem template that persists every simulated sample mean, '
                'theoretical comparisons, exact counts, metrics, metadata, and plots.'))


def deterministic_plan(request: str,parent_plan=None):
    seed=42;match=re.search(r'(?:seed|random seed)\s*(?:=|of|:)??\s*(\d+)',request,re.I)
    if match:seed=int(match.group(1))
    elif parent_plan:seed=int(parent_plan.get('random_seed',42))+1
    if supports_classification(request) or (parent_plan and parent_plan.get('experiment_type')=='classification_comparison'):
        plan=classification_plan(request,seed=seed)
        if parent_plan and _requested_seed_count(request)==1:
            seeds=[seed+index for index in range(len(parent_plan.get('random_seeds') or [seed]))]
            plan=plan.model_copy(update={'dataset':DatasetSpec.model_validate(parent_plan['dataset']),
                'methods':parent_plan['methods'],'metrics':parent_plan['metrics'],'random_seeds':seeds,
                'expected_training_runs':len(seeds)*len(parent_plan['methods']),'reproduction_level':'adapted',
                'limitations':list(parent_plan.get('limitations',[]))+['Follow-up version of a previous run']})
        return plan
    if supports_clt(request) or (parent_plan and parent_plan.get('parameters',{}).get('template')=='clt_exponential' and not re.search(r'\b(normal|gaussian|uniform|poisson|bernoulli|gamma|weibull)\b',request,re.I)):
        plan=clt_plan(request,seed=seed)
        if parent_plan:
            explicit_sizes=bool(re.search(r'(?:sample\s+sizes?|n\s*(?:=|in))\s*(?:of|:|=|in)?\s*[\[{(]?\s*\d+(?:\s*[,/]\s*\d+)+',request,re.I))
            explicit_count=bool(re.search(r'\d[\d,]*\s+(?:independent\s+)?sample means|\d[\d,]*\s+(?:repetitions|replicates|simulations|trials|runs)\b|(?:repeat|simulate|run)\s+\d[\d,]*\s+times',request,re.I))
            parameters=dict(parent_plan.get('parameters',plan.parameters))
            if explicit_sizes:parameters['sample_sizes']=plan.parameters['sample_sizes']
            if explicit_count:parameters['simulations_per_size']=plan.parameters['simulations_per_size']
            expected=parameters['simulations_per_size']*len(parameters['sample_sizes'])
            plan=plan.model_copy(update={'parameters':parameters,
                'expected_simulations':expected,
                'reproduction_level':'adapted',
                'limitations':list(parent_plan.get('limitations',[]))+['Follow-up version of a previous run']})
        return plan
    return None


def bundle_for_plan(plan: ExperimentPlan):
    if plan.experiment_type=='classification_comparison' and plan.parameters.get('template')=='classification_repeated_splits':return classification_bundle(plan)
    if plan.parameters.get('template')=='clt_exponential':return clt_bundle(plan)
    return None