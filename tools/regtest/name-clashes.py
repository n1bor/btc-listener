import re, os, itertools, collections
mods = {}          # module-name -> (path, set of fn names)
deps = {}          # module-name -> [dep names]
for root in ('domain','infra','app'):
    for f in sorted(os.listdir(root)):
        if not f.endswith('.av'): continue
        p = os.path.join(root,f)
        s = open(p).read()
        m = re.match(r'module (\w+)', s)
        name = root.capitalize()+'.'+m.group(1) if root!='app' else 'App.'+m.group(1)
        if root=='domain': name='Domain.'+m.group(1)
        if root=='infra': name='Infra.'+m.group(1)
        fns = set(re.findall(r'^fn (\w+)', s, re.M))
        types = set(re.findall(r'^(?:record|type) (\w+)', s, re.M))
        mods[name]=(p, fns|types)
        d = re.search(r'^    depends \[(.*)\]', s, re.M)
        deps[name]=[x.strip() for x in d.group(1).split(',')] if d else []
mods['Bytes']=('<builtin>',set())
bad=0
for name,(p,_) in mods.items():
    ds=[d for d in deps.get(name,[]) if d in mods]
    for a,b in itertools.combinations(ds,2):
        shared = mods[a][1] & mods[b][1]
        if shared:
            bad+=1
            print(f"{p}: depends on both {a} and {b}, which share: {sorted(shared)}")
print("clashes:", bad)
