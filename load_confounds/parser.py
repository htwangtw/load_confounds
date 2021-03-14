"""Flexible method to load confounds generated by fMRIprep.

Authors: Hanad Sharmarke, Dr. Pierre Bellec, Francois Paugam
"""
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import scale
import warnings
import os
import json


# Global variables listing the admissible types of noise components
all_confounds = [
    "motion",
    "high_pass",
    "wm_csf",
    "global",
    "compcor",
    "ica_aroma",
    "censoring",
]


def _add_suffix(params, model):
    """
    Add suffixes to a list of parameters.
    Suffixes includes derivatives, power2 and full
    """
    params_full = params.copy()
    suffix = {
        "basic": {},
        "derivatives": {"derivative1"},
        "power2": {"power2"},
        "full": {"derivative1", "power2", "derivative1_power2"},
    }
    for par in params:
        for suff in suffix[model]:
            params_full.append(f"{par}_{suff}")
    return params_full


def _check_params(confounds_raw, params):
    """Check that specified parameters can be found in the confounds."""
    for par in params:
        if not par in confounds_raw.columns:
            raise ValueError(
                f"The parameter {par} cannot be found in the available confounds. You may want to use a different denoising strategy'"
            )

    return None


def _find_confounds(confounds_raw, keywords):
    """Find confounds that contain certain keywords."""
    list_confounds = []
    for key in keywords:
        key_found = False
        for col in confounds_raw.columns:
            if key in col:
                list_confounds.append(col)
                key_found = True
        if not key_found:
            raise ValueError(f"could not find any confound with the key {key}")
    return list_confounds


def _load_global(confounds_raw, global_signal):
    """Load the regressors derived from the global signal."""
    global_params = _add_suffix(["global_signal"], global_signal)
    _check_params(confounds_raw, global_params)
    return confounds_raw[global_params]


def _load_wm_csf(confounds_raw, wm_csf):
    """Load the regressors derived from the white matter and CSF masks."""
    wm_csf_params = _add_suffix(["csf", "white_matter"], wm_csf)
    _check_params(confounds_raw, wm_csf_params)
    return confounds_raw[wm_csf_params]


def _load_high_pass(confounds_raw):
    """Load the high pass filter regressors."""
    high_pass_params = _find_confounds(confounds_raw, ["cosine"])
    return confounds_raw[high_pass_params]


def _select_compcor(compcor_cols, n_compcor, compcor_mask):
    """retain a specified number of compcor components."""
    # only select if not "auto", or less components are requested than there actually is
    if (n_compcor != "auto") and (n_compcor < len(compcor_cols)):
        compcor_cols = compcor_col[0:n_compcor]
    return compcor_cols


def _label_compcor(confounds_json, prefix, n_compcor, compcor_mask):
    """Builds list for the number of compcor components."""
    # all possible compcor confounds, mixing different types of mask
    all_compcor = [
        comp for comp in confounds_json.keys() if f"{prefix}_comp_cor" in comp
    ]

    # loop and only retain the relevant confounds
    compcor_cols = []
    for nn in range(len(all_compcor)):
        nn_str = str(nn).zfill(2)
        compcor_col = f"{prefix}_comp_cor_{nn_str}"
        if (prefix == "t") or (
            (prefix == "a") and (confounds_json[compcor_col]["Mask"] == compcor_mask)
        ):
            compcor_cols.append(compcor_col)

    return _select_compcor(compcor_cols, n_compcor, compcor_mask)


def _load_acompcor(confounds_json, n_compcor, acompcor_combined):
    if acompcor_combined:
        compcor_cols = _label_compcor(confounds_json, "a", n_compcor, "combined")
    else:
        compcor_cols = _label_compcor(confounds_json, "a", n_compcor, "WM")
        compcor_cols.extend(_label_compcor(confounds_json, "a", n_compcor, "CSF"))
    return compcor_cols

def _load_compcor(confounds_raw, confounds_json, compcor, n_compcor, acompcor_combined):
    """Load compcor regressors."""
    if compcor == "anat":
        compcor_cols = _load_acompcor(confounds_json, n_compcor, acompcor_combined)
        
    if compcor == "temp":
        compcor_cols = _label_compcor(confounds_json, "t", n_compcor, acompcor_combined)

    if compcor == "full":
        compcor_cols = _label_compcor(confounds_json, "a", n_compcor, acompcor_combined)
        compcor_cols.extend(
            _label_compcor(confounds_json, "t", n_compcor, acompcor_combined)
        )

    _check_params(confounds_raw, compcor_cols)
    return confounds_raw[compcor_cols]


def _load_motion(confounds_raw, motion, n_motion):
    """Load the motion regressors."""
    motion_params = _add_suffix(
        ["trans_x", "trans_y", "trans_z", "rot_x", "rot_y", "rot_z"], motion
    )
    _check_params(confounds_raw, motion_params)
    confounds_motion = confounds_raw[motion_params]

    # Optionally apply PCA reduction
    if n_motion > 0:
        confounds_motion = _pca_motion(confounds_motion, n_components=n_motion)

    return confounds_motion


def _load_ica_aroma(confounds_raw):
    """Load the ICA-AROMA regressors."""
    ica_aroma_params = _find_confounds(confounds_raw, ["aroma"])
    return confounds_raw[ica_aroma_params]


def _pca_motion(confounds_motion, n_components):
    """Reduce the motion paramaters using PCA."""
    n_available = confounds_motion.shape[1]
    if n_components > n_available:
        raise ValueError(
            f"User requested n_motion={n_components} motion components, but found only {n_available}."
        )
    confounds_motion = confounds_motion.dropna()
    confounds_motion_std = scale(
        confounds_motion, axis=0, with_mean=True, with_std=True
    )
    pca = PCA(n_components=n_components)
    motion_pca = pd.DataFrame(pca.fit_transform(confounds_motion_std))
    motion_pca.columns = ["motion_pca_" + str(col + 1) for col in motion_pca.columns]
    return motion_pca


def _load_censoring(confounds_raw, censoring, fd_thresh, std_dvars_thresh):
    """Perform basic censoring - Remove volumes if framewise displacement exceeds threshold"""
    """Power, Jonathan D., et al. "Steps toward optimizing motion artifact removal in functional connectivity MRI; a reply to Carp." Neuroimage 76 (2013)."""
    n_scans = len(confounds_raw)
    # Get indices of fd outliers
    fd_outliers = np.where(confounds_raw["framewise_displacement"] > fd_thresh)[0]
    dvars_outliers = np.where(confounds_raw["std_dvars"] > std_dvars_thresh)[0]
    combined_outliers = np.sort(
        np.unique(np.concatenate((fd_outliers, dvars_outliers)))
    )
    # Do optimized scrubbing if desired
    if censoring == "optimized":
        combined_outliers = _optimize_censoring(combined_outliers, n_scans)
    # Make one-hot encoded motion outlier regressors
    motion_outlier_regressors = pd.DataFrame(
        np.transpose(np.eye(n_scans)[combined_outliers]).astype(int)
    )
    column_names = [
        "motion_outlier_" + str(num)
        for num in range(np.shape(motion_outlier_regressors)[1])
    ]
    motion_outlier_regressors.columns = column_names
    return motion_outlier_regressors


def _optimize_censoring(fd_outliers, n_scans):
    """Perform optimized censoring. After censoring volumes, further remove continuous segments containing fewer than 5 volumes"""
    """Power, Jonathan D., et al. "Methods to detect, characterize, and remove motion artifact in resting state fMRI." Neuroimage 84 (2014): 320-341."""
    # Start by checking if the beginning continuous segment is fewer than 5 volumes
    if fd_outliers[0] < 5:
        fd_outliers = np.asarray(list(range(fd_outliers[0])) + list(fd_outliers))
    # Do the same for the ending segment of scans
    if n_scans - (fd_outliers[-1] + 1) < 5:
        fd_outliers = np.asarray(
            list(fd_outliers) + list(range(fd_outliers[-1], n_scans))
        )
    # Now do everything in between
    fd_outlier_ind_diffs = np.diff(fd_outliers)
    short_segments_inds = np.where(
        np.logical_and(fd_outlier_ind_diffs > 1, fd_outlier_ind_diffs < 6)
    )[0]
    for ind in short_segments_inds:
        fd_outliers = np.asarray(
            list(fd_outliers) + list(range(fd_outliers[ind] + 1, fd_outliers[ind + 1]))
        )
    fd_outliers = np.sort(np.unique(fd_outliers))
    return fd_outliers


def _sanitize_strategy(strategy):
    """Defines the supported denoising strategies."""
    if isinstance(strategy, list):
        for conf in strategy:
            if not conf in all_confounds:
                raise ValueError(f"{conf} is not a supported type of confounds.")
    else:
        raise ValueError("strategy needs to be a list of strings")
    return strategy


def _confounds_to_df(confounds_raw):
    """Load raw confounds as a pandas DataFrame."""
    if "nii" in confounds_raw[-6:]:
        suffix = "_space-" + confounds_raw.split("space-")[1]
        confounds_raw = confounds_raw.replace(suffix, "_desc-confounds_timeseries.tsv",)
        # fmriprep has changed the file suffix between v20.1.1 and v20.2.0 with respect to BEP 012.
        # cf. https://neurostars.org/t/naming-change-confounds-regressors-to-confounds-timeseries/17637
        # Check file with new naming scheme exists or replace, for backward compatibility.
        if not os.path.exists(confounds_raw):
            confounds_raw = confounds_raw.replace(
                "_desc-confounds_timeseries.tsv", "_desc-confounds_regressors.tsv",
            )

    # Load JSON file
    with open(confounds_raw.replace("tsv", "json"), "rb") as f:
        confounds_json = json.load(f)

    confounds_raw = pd.read_csv(confounds_raw, delimiter="\t", encoding="utf-8")

    return confounds_raw, confounds_json


def _sanitize_confounds(confounds_raw):
    """Make sure the inputs are in the correct format."""
    # we want to support loading a single set of confounds, instead of a list
    # so we hack it
    flag_single = isinstance(confounds_raw, str) or isinstance(
        confounds_raw, pd.DataFrame
    )
    if flag_single:
        confounds_raw = [confounds_raw]

    return confounds_raw, flag_single


def _confounds_to_ndarray(confounds, demean):
    """Convert confounds from a pandas dataframe to a numpy array."""
    # Convert from DataFrame to numpy ndarray
    labels = confounds.columns
    confounds = confounds.values

    # Derivatives have NaN on the first row
    # Replace them by estimates at second time point,
    # otherwise nilearn will crash.
    mask_nan = np.isnan(confounds[0, :])
    confounds[0, mask_nan] = confounds[1, mask_nan]

    # Optionally demean confounds
    if demean:
        confounds = scale(confounds, axis=0, with_std=False)

    return confounds, labels


class Confounds:
    """
    Confounds from fmriprep

    Parameters
    ----------
    strategy : list of strings
        The type of noise confounds to include.
        "motion" head motion estimates.
        "high_pass" discrete cosines covering low frequencies.
        "wm_csf" confounds derived from white matter and cerebrospinal fluid.
        "global" confounds derived from the global signal.
        "ica_aroma" confounds derived from ICA-AROMA.

    motion : string, optional
        Type of confounds extracted from head motion estimates.
        "basic" translation/rotation (6 parameters)
        "power2" translation/rotation + quadratic terms (12 parameters)
        "derivatives" translation/rotation + derivatives (12 parameters)
        "full" translation/rotation + derivatives + quadratic terms + power2d derivatives (24 parameters)

    n_motion : float
        Number of pca components to keep from head motion estimates.
        If the parameters is strictly comprised between 0 and 1, a principal component
        analysis is applied to the motion parameters, and the number of extracted
        components is set to exceed `n_motion` percent of the parameters variance.
        If the n_components = 0, then no PCA is performed.

    fd_thresh : float, optional
        Framewise displacement threshold for censoring (default = 0.2 mm)

    std_dvars_thresh : float, optional
        Standardized DVARS threshold for censoring (default = 3)

    wm_csf : string, optional
        Type of confounds extracted from masks of white matter and cerebrospinal fluids.
        "basic" the averages in each mask (2 parameters)
        "power2" averages and quadratic terms (4 parameters)
        "derivatives" averages and derivatives (4 parameters)
        "full" averages + derivatives + quadratic terms + power2d derivatives (8 parameters)

    global_signal : string, optional
        Type of confounds extracted from the global signal.
        "basic" just the global signal (1 parameter)
        "power2" global signal and quadratic term (2 parameters)
        "derivatives" global signal and derivative (2 parameters)
        "full" global signal + derivatives + quadratic terms + power2d derivatives (4 parameters)

    compcor : string, optional
        Type of confounds extracted from a component based noise correction method
        "anat" noise components calculated using anatomical compcor
        "temp" noise components calculated using temporal compcor
        "full" noise components calculated using both temporal and anatomical

    n_compcor : int or "auto", optional
        The number of noise components to be extracted.
        Default is "auto": select all components (50% variance explained by fMRIPrep defaults)

    acompcor_combined: boolean, optional
        If true, use components generated from the combined white matter and csf
        masks. Otherwise, components are generated from each mask separately and then
        concatenated.

    demean : boolean, optional
        If True, the confounds are standardized to a zero mean (over time).
        This step is critical if the confounds are regressed out of time series
        using nilearn with no or zscore standardization, but should be turned off
        with "spc" normalization.

    Attributes
    ----------
    `confounds_` : ndarray
        The confounds loaded using the specified model

    `columns_`: list of str
        The labels of the different confounds

    Notes
    -----
    The predefined strategies implemented in this class are from
    adapted from (Ciric et al. 2017). Band-pass filter is replaced
    by high-pass filter, as high frequencies have been shown to carry
    meaningful signal for connectivity analysis.

    References
    ----------
    Ciric et al., 2017 "Benchmarking of participant-level confound regression
    strategies for the control of motion artifact in studies of functional
    connectivity" Neuroimage 154: 174-87
    https://doi.org/10.1016/j.neuroimage.2017.03.020
    """

    def __init__(
        self,
        strategy=["motion", "high_pass", "wm_csf"],
        motion="full",
        n_motion=0,
        censoring="basic",
        fd_thresh=0.2,
        std_dvars_thresh=3,
        wm_csf="basic",
        global_signal="basic",
        compcor="anat",
        acompcor_combined=True,
        n_compcor="auto",
        demean=True,
    ):
        """Default parameters."""
        self.strategy = _sanitize_strategy(strategy)
        self.motion = motion
        self.n_motion = n_motion
        self.censoring = censoring
        self.fd_thresh = fd_thresh
        self.std_dvars_thresh = std_dvars_thresh
        self.wm_csf = wm_csf
        self.global_signal = global_signal
        self.compcor = compcor
        self.acompcor_combined = acompcor_combined
        self.n_compcor = n_compcor
        self.demean = demean

    def load(self, confounds_raw):
        """
        Load fMRIprep confounds

        Parameters
        ----------
        confounds_raw : path to tsv or nii file(s), optionally as a list.
            Raw confounds from fmriprep. If a nii is provided, the companion
            tsv will be automatically detected.

        Returns
        -------
        confounds :  ndarray or list of ndarray
            A reduced version of fMRIprep confounds based on selected strategy and flags.
            An intercept is automatically added to the list of confounds.
        """
        confounds_raw, flag_single = _sanitize_confounds(confounds_raw)
        confounds_out = []
        columns_out = []
        for file in confounds_raw:
            conf, col = self._load_single(file)
            confounds_out.append(conf)
            columns_out.append(col)

        # If a single input was provided,
        # send back a single output instead of a list
        if flag_single:
            confounds_out = confounds_out[0]
            columns_out = columns_out[0]

        self.confounds_ = confounds_out
        self.columns_ = columns_out
        return confounds_out

    def _load_single(self, confounds_raw):
        """Load a single confounds file from fmriprep."""
        # Convert tsv file to pandas dataframe
        confounds_raw, confounds_json = _confounds_to_df(confounds_raw)

        confounds = pd.DataFrame()

        if "motion" in self.strategy:
            confounds_motion = _load_motion(confounds_raw, self.motion, self.n_motion)
            confounds = pd.concat([confounds, confounds_motion], axis=1)

        if "censoring" in self.strategy:
            confounds_censoring = _load_censoring(
                confounds_raw, self.censoring, self.fd_thresh, self.std_dvars_thresh
            )
            confounds = pd.concat([confounds, confounds_censoring], axis=1)

        if "high_pass" in self.strategy:
            confounds_high_pass = _load_high_pass(confounds_raw)
            confounds = pd.concat([confounds, confounds_high_pass], axis=1)

        if "wm_csf" in self.strategy:
            confounds_wm_csf = _load_wm_csf(confounds_raw, self.wm_csf)
            confounds = pd.concat([confounds, confounds_wm_csf], axis=1)

        if "global" in self.strategy:
            confounds_global_signal = _load_global(confounds_raw, self.global_signal)
            confounds = pd.concat([confounds, confounds_global_signal], axis=1)

        if "compcor" in self.strategy:
            confounds_compcor = _load_compcor(
                confounds_raw,
                confounds_json,
                self.compcor,
                self.n_compcor,
                self.acompcor_combined,
            )
            confounds = pd.concat([confounds, confounds_compcor], axis=1)

        if "ica_aroma" in self.strategy:
            confounds_ica_aroma = _load_ica_aroma(confounds_raw)
            confounds = pd.concat([confounds, confounds_ica_aroma], axis=1)

        confounds, labels = _confounds_to_ndarray(confounds, self.demean)

        return confounds, labels
