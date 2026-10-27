"""Real-coded, constrained NSGA-II, archive MOPSO and annealing searches.

All variables use [0,1] bounds. The evaluator performs physical decoding/repair.
Rows contain x, objectives, cv and eligible. No physical model is embedded here.
"""
import math
import numpy as np


def dominates(a, b):
    if a['eligible'] != b['eligible']:
        return a['eligible']
    if not a['eligible']:
        return a['cv'] < b['cv']
    av, bv = np.array(a['objectives']), np.array(b['objectives'])
    return bool(np.all(av <= bv) and np.any(av < bv))


def fronts(rows):
    remaining = list(range(len(rows)))
    result = []
    while remaining:
        front = [i for i in remaining if not any(dominates(rows[j],rows[i]) for j in remaining if j != i)]
        result.append(front)
        removed = set(front)
        remaining = [i for i in remaining if i not in removed]
    return result


def crowding(rows, indices):
    distances = {i:0. for i in indices}
    for objective in range(2):
        order = sorted(indices,key=lambda i:rows[i]['objectives'][objective])
        if not order: continue
        distances[order[0]] = distances[order[-1]] = float('inf')
        scale = rows[order[-1]]['objectives'][objective]-rows[order[0]]['objectives'][objective]
        if not np.isfinite(scale) or scale <= 0: continue
        for j in range(1,len(order)-1):
            distances[order[j]] += (rows[order[j+1]]['objectives'][objective]-rows[order[j-1]]['objectives'][objective])/scale
    return distances


def survive(rows, size):
    selected = []
    for front in fronts(rows):
        d = crowding(rows,front)
        selected.extend(sorted(front,key=lambda i:d[i],reverse=True)[:size-len(selected)])
        if len(selected) >= size: break
    return [rows[i] for i in selected]


def mutate(x, rng, eta=20.):
    x = np.array(x, dtype=float).copy()
    for j,y in enumerate(x):
        if rng.random() > 1/len(x): continue
        u = rng.random()
        if u <= .5:
            delta=(2*u+(1-2*u)*(1-y)**(eta+1))**(1/(eta+1))-1
        else:
            delta=1-(2*(1-u)+2*(u-.5)*y**(eta+1))**(1/(eta+1))
        x[j] = np.clip(y+delta,0,1)
    return x


def crossover(a,b,rng,eta=15.):
    """Bounded simulated binary crossover, followed by polynomial mutation."""
    child = np.array(a,dtype=float).copy()
    if rng.random() > .9: return mutate(child,rng)
    for j,(pa,pb) in enumerate(zip(a,b)):
        if rng.random() > .5 or abs(pa-pb)<1e-14: continue
        lo,hi=min(pa,pb),max(pa,pb)
        u=rng.random()
        beta=1+2*lo/(hi-lo); alpha=2-beta**(-(eta+1))
        q=(u*alpha)**(1/(eta+1)) if u<=1/alpha else (1/(2-u*alpha))**(1/(eta+1))
        c1=.5*(lo+hi-q*(hi-lo))
        beta=1+2*(1-hi)/(hi-lo); alpha=2-beta**(-(eta+1))
        q=(u*alpha)**(1/(eta+1)) if u<=1/alpha else (1/(2-u*alpha))**(1/(eta+1))
        c2=.5*(lo+hi+q*(hi-lo))
        child[j]=np.clip(c1 if rng.random()<.5 else c2,0,1)
    return mutate(child,rng)


def optimize(algorithm, evaluate, dimensions, budget, seed, population=32):
    """Exactly budget evaluator calls; returns every run-level evaluation.

    AMOSA is the documented single-chain, constrained archive adaptation; it is
    not a claim of bit-for-bit equivalence with a third-party AMOSA package.
    """
    if budget < 1: raise ValueError('Evaluation budget must be positive')
    rng=np.random.default_rng(seed)
    history=[]
    def call(x):
        row=dict(evaluate(np.clip(x,0,1)))
        row['x']=list(map(float,np.clip(x,0,1)))
        history.append(row)
        return row
    size=min(population,budget)
    if algorithm=='AMOSA':
        current=call(rng.random(dimensions)); archive=[current]
        temperature=1/(-math.log(.7))
        for iteration in range(1,budget):
            trial=call(mutate(np.clip(np.array(current['x'])+rng.normal(0,.12,dimensions),0,1),rng))
            accept=dominates(trial,current)
            if not accept and (not current['eligible'] or trial['eligible']):
                if current['eligible'] and trial['eligible']:
                    values=np.array([r['objectives'] for r in archive]+[current['objectives'],trial['objectives']])
                    scales=np.maximum(np.ptp(values,axis=0),1e-6)
                    amount=float(np.maximum(0,(np.array(trial['objectives'])-current['objectives'])/scales).sum())
                else:
                    amount=max(0.,trial['cv']-current['cv'])
                accept=rng.random()<math.exp(-min(amount/max(temperature,1e-12),700))
            if accept: current=trial
            archive=survive(archive+[trial],min(population,len(archive)+1))
            archive=[archive[i] for i in fronts(archive)[0]]
            temperature*=.01**(1/max(1,budget-1))
        return history
    pop=[call(rng.random(dimensions)) for _ in range(size)]
    if algorithm=='RNSGA-II':
        while len(history)<budget:
            ranking={}; diversity={}
            for rank,front in enumerate(fronts(pop)):
                ranking.update({i:rank for i in front}); diversity.update(crowding(pop,front))
            def tournament():
                a,b=rng.integers(0,len(pop),2)
                return pop[min((a,b),key=lambda i:(ranking[i],-diversity[i]))]['x']
            offspring=[call(crossover(tournament(),tournament(),rng))
                       for _ in range(min(size,budget-len(history)))]
            pop=survive(pop+offspring,size)
    elif algorithm=='MOPSO':
        positions=np.array([r['x'] for r in pop]); velocity=np.zeros_like(positions)
        personal=list(pop); archive=survive(pop,size)
        while len(history)<budget:
            inertia=.9-.5*len(history)/budget
            front=fronts(archive)[0]; distances=crowding(archive,front)
            for i in range(size):
                if len(history)>=budget: break
                leaders=rng.choice(front,2)
                leader=archive[max(leaders,key=lambda j:distances[j])]
                velocity[i]=inertia*velocity[i]+1.5*rng.random(dimensions)*(np.array(personal[i]['x'])-positions[i])+1.5*rng.random(dimensions)*(np.array(leader['x'])-positions[i])
                positions[i]=np.clip(positions[i]+np.clip(velocity[i],-.25,.25),0,1)
                row=call(positions[i])
                if dominates(row,personal[i]) or (not dominates(personal[i],row) and rng.random()<.5): personal[i]=row
                archive=survive(archive+[row],size)
    else:
        raise ValueError(f'Unknown algorithm: {algorithm}')
    return history
