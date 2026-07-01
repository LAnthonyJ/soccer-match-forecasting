"""Shared test warnings filter — suppress known pickle version mismatches."""
import os
import warnings

os.environ.setdefault("XGB_VERBOSITY", "0")

# xgboost pickle version warning (emitted via ctypes callback as UserWarning)
warnings.filterwarnings("ignore", category=UserWarning)
# sklearn pickle version mismatch
try:
    from sklearn.exceptions import InconsistentVersionWarning
    warnings.filterwarnings("ignore", category=InconsistentVersionWarning)
except ImportError:
    pass
