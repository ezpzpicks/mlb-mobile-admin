from __future__ import annotations
import json
from research import cfb_2026_anchor_extension_search as ext

FEATURES=['gamecontrol_diff','def_returning_diff','explosive_diff','current_power_margin','havoc_off_diff']
ALPHA=4.0
CAP=8.0

df,_=ext.prepare()
m=ext.fit(df,FEATURES,ALPHA)
out={
  'features':FEATURES,
  'alpha':ALPHA,
  'cap':CAP,
  'mu':{k:float(m['mu'][k]) for k in FEATURES},
  'sd':{k:float(m['sd'][k]) for k in FEATURES},
  'b':{'intercept':float(m['b'][0]), **{k:float(v) for k,v in zip(FEATURES,m['b'][1:])}},
}
print(json.dumps(out,indent=2))
open('research/results/cfb_2026_base5_frozen_params.json','w').write(json.dumps(out,indent=2)+'\n')
