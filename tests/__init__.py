import warnings

warnings.filterwarnings("ignore", message=r".*loading a serialized model.*")
try:
    from sklearn.exceptions import InconsistentVersionWarning
    warnings.filterwarnings("ignore", category=InconsistentVersionWarning)
except ImportError:
    pass
