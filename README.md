# (Symmetric) Gradient Domain Machine Learning (sGDML)

#### Requirements:
- Python 2.7
- NumPy (>=1.13.0)
- SciPy

## Getting started

#### Clone the repository

`git clone https://github.com/stefanch/sGDML.git`

`cd sGDML`

##### ...or update your local copy

`git pull origin master`

#### Reconstruct your first force field

`python tools/download_datasets.py`

`python assist.py train datasets/npz/ethanol.npz 200 1000`

#### Use you first force field

```python
import sys
import numpy as np
from gdml_predict import GDMLPredict

model_path = 'models/npz/ethanol.npz'
try:
   model = np.load(model_path)
except:
   sys.exit(’ERROR: Reading file failed.’)
   
gdml = GDMLPredict(model)
e,f = gdml.predict(r) # e: (1,)  e: (1,3*n_atoms)

```

## References

* [1] Chmiela, S., Tkatchenko, A., Sauceda, H. E., Poltavsky, Igor, Schütt, K. T., Müller, K.-R.,
*Machine Learning of Accurate Energy-conserving Molecular Force Fields.*
Science Advances, 3(5), e1603015 (2017)   
[10.1126/sciadv.1603015](http://dx.doi.org/10.1126/sciadv.1603015)

* [2] Chmiela, S., Sauceda, H., Müller, K.-R., & Tkatchenko, A.,
*Towards Exact Molecular Dynamics Simulations with Machine-Learned Force Fields.*
arXiv preprint, 1802.09238 (2018)   
[arXiv:1802.09238](https://arxiv.org/abs/1802.09238)