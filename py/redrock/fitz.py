"""
redrock.fitz
============

Functions for fitting minima of chi^2 results.
"""

from __future__ import absolute_import, division, print_function

import sys
import numpy as np
import scipy.constants

from . import constants

from . import zscan

from .rebin import rebin_template

from .zscan import calc_zchi2_one, spectral_data

from .zwarning import ZWarningMask as ZW

from .utils import transmitted_flux_fraction

def get_dv(z, zref):
    """Returns velocity difference in km/s for two redshifts

    Args:
        z (float): redshift for comparison.
        zref (float): reference redshift.

    Returns:
        (float): the velocity difference.

    """

    c = (scipy.constants.speed_of_light/1000.) #- km/s
    dv = c * (z - zref) / (1.0 + zref)

    return dv


def find_minima(x):
    """Return indices of local minima of x, including edges.

    The indices are sorted small to large.

    Note:
        this is somewhat conservative in the case of repeated values:
        find_minima([1,1,1,2,2,2]) -> [0,1,2,4,5]

    Args:
        x (array-like): The data array.

    Returns:
        (array): The indices.

    """
    x = np.asarray(x)
    ii = np.where(np.r_[True, x[1:]<=x[:-1]] & np.r_[x[:-1]<=x[1:], True])[0]

    jj = np.argsort(x[ii])

    return ii[jj]


def minfit(x, y):
    """Fits y = y0 + ((x-x0)/xerr)**2

    See redrock.zwarning.ZWarningMask.BAD_MINFIT for zwarn failure flags

    Args:
        x (array): x values.
        y (array): y values.

    Returns:
        (tuple):  (x0, xerr, y0, zwarn) where zwarn=0 is good fit.

    """
    if len(x) < 3:
        return (-1,-1,-1,ZW.BAD_MINFIT)

    try:
        #- y = a x^2 + b x + c
        a,b,c = np.polyfit(x,y,2)
    except np.linalg.LinAlgError:
        return (-1,-1,-1,ZW.BAD_MINFIT)

    if a == 0.0:
        return (-1,-1,-1,ZW.BAD_MINFIT)

    #- recast as y = y0 + ((x-x0)/xerr)^2
    x0 = -b / (2*a)
    y0 = -(b**2) / (4*a) + c

    zwarn = 0
    if (x0 <= np.min(x)) or (np.max(x) <= x0):
        zwarn |= ZW.BAD_MINFIT
    if (y0<=0.):
        zwarn |= ZW.BAD_MINFIT

    if a > 0.0:
        xerr = 1 / np.sqrt(a)
    else:
        xerr = 1 / np.sqrt(-a)
        zwarn |= ZW.BAD_MINFIT

    return (x0, xerr, y0, zwarn)


def fitz(zchi2, redshifts, spectra, template, nminima=3):
    """Refines redshift measurement around up to nminima minima.

    TODO:
        if there are fewer than nminima minima, consider padding.

    Args:
        zchi2 (array): chi^2 values for each redshift.
        redshifts (array): the redshift values.
        spectra (list): list of Spectrum objects at different wavelengths
            grids.
        template (Template): the template for this fit.
        nminima (int): the number of minima to consider.

    Returns:
        Table: the fit parameters for the minima.

    """
    assert len(zchi2) == len(redshifts)

    nbasis = template.nbasis

    # Build dictionary of wavelength grids
    dwave = dict()
    for s in spectra:
        if s.wavehash not in dwave:
            dwave[s.wavehash] = s.wave

    (weights, flux, wflux) = spectral_data(spectra)

    results = list()

    for imin in find_minima(zchi2):
        if len(results) == nminima:
            break

        #- Skip this minimum if it is within constants.max_velo_diff km/s of a
        # previous one dv is in km/s
        zprev = np.array([tmp['z'] for tmp in results])
        dv = get_dv(z=redshifts[imin],zref=zprev)
        if np.any(np.abs(dv) < constants.max_velo_diff):
            continue

        #- Sample more finely around the minimum
        ilo = max(0, imin-1)
        ihi = min(imin+1, len(zchi2)-1)
        zz = np.linspace(redshifts[ilo], redshifts[ihi], 15)
        nz = len(zz)

        zzchi2 = np.zeros(nz, dtype=np.float64)
        zzcoeff = np.zeros((nz, nbasis), dtype=np.float64)

        for i, z in enumerate(zz):
            binned = rebin_template(template, z, dwave)
            for k in list(dwave.keys()):
                binned[k][:,0] *= transmitted_flux_fraction(z,dwave[k])

            tmp_weights = weights.copy()
            tmp_wflux = wflux.copy()
            tmp_wave = np.concatenate([ s.wave for s in spectra ])
            tmp_waveRF = tmp_wave/(1.+z)
            T = transmitted_flux_fraction(z,tmp_wave)
            w = (T!=1.) & (tmp_weights>1.)
            tmp_weights[w] = 1.
            for l in [1215.67,1548.2049]:
                w = tmp_waveRF<l
                if w.sum()<50:
                    w &= (tmp_weights>1.)
                    tmp_weights[w] = 1.

            tmp_wflux = tmp_weights*flux

            zzchi2[i], zzcoeff[i] = calc_zchi2_one(spectra, tmp_weights, flux,
                tmp_wflux, binned)

        #- fit parabola to 3 points around minimum
        i = min(max(np.argmin(zzchi2),1), len(zz)-2)
        zmin, sigma, chi2min, zwarn = minfit(zz[i-1:i+2], zzchi2[i-1:i+2])

        try:
            binned = rebin_template(template, zmin, dwave)
            for k in list(dwave.keys()):
                binned[k][:,0] *= transmitted_flux_fraction(zmin,dwave[k])

            tmp_weights = weights.copy()
            tmp_wflux = wflux.copy()
            tmp_wave = np.concatenate([ s.wave for s in spectra ])
            T = transmitted_flux_fraction(z,tmp_wave)
            w = (T!=1.) & (tmp_weights>1.)
            tmp_weights[w] = 1.
            for l in [1215.67,1548.2049]:
                w = tmp_waveRF<l
                if w.sum()<50:
                    w &= (tmp_weights>1.)
                    tmp_weights[w] = 1.
            tmp_wflux = tmp_weights*flux

            coeff = calc_zchi2_one(spectra, tmp_weights, flux, tmp_wflux,
                binned)[1]
        except ValueError as err:
            if zmin<redshifts[0] or redshifts[-1]<zmin:
                #- beyond redshift range can be invalid for template
                coeff = np.zeros(template.nbasis)
                zwarn |= ZW.Z_FITLIMIT
                zwarn |= ZW.BAD_MINFIT
            else:
                #- Unknown problem; re-raise error
                raise err

        zbest = zmin
        zerr = sigma

        #- Initial minimum or best fit too close to edge of redshift range
        if zbest < redshifts[1] or zbest > redshifts[-2]:
            zwarn |= ZW.Z_FITLIMIT
        if zmin < redshifts[1] or zmin > redshifts[-2]:
            zwarn |= ZW.Z_FITLIMIT

        #- parabola minimum outside fit range; replace with min of scan
        if zbest < zz[0] or zbest > zz[-1]:
            zwarn |= ZW.BAD_MINFIT
            imin = np.where(zbest == np.min(zbest))[0][0]
            zbest = zz[imin]
            chi2min = zzchi2[imin]

        #- Skip this better defined minimum if it is within
        #- constants.max_velo_diff km/s of a previous one
        zprev = np.array([tmp['z'] for tmp in results])
        dv = get_dv(z=zbest, zref=zprev)
        if np.any(np.abs(dv) < constants.max_velo_diff):
            continue

        results.append(dict(z=zbest, zerr=zerr, zwarn=zwarn,
            chi2=chi2min, zz=zz, zzchi2=zzchi2,
            coeff=coeff))

    #- Sort results by chi2min; detailed fits may have changed order
    ii = np.argsort([tmp['chi2'] for tmp in results])
    results = [results[i] for i in ii]

    #- Convert list of dicts -> Table
    from astropy.table import Table
    results = Table(results)

    assert len(results) > 0

    return results
