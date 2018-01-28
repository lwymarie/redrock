"""
Classes and functions for targets and their spectra.
"""

from __future__ import absolute_import, division, print_function

import sys

import numpy as np
import scipy.sparse
from collections import OrderedDict

from astropy.table import Table

from .utils import mp_array, distribute_work
from . import mpsharedmem

class Spectrum(object):
    """Simple container class for an individual spectrum.

    Args:
        wave (array): the wavelength grid.
        flux (array): the flux values.
        ivar (array): the inverse variance.
        R (scipy.sparse.dia_matrix): the resolution matrix in band diagonal
            format.
        Rcsr (scipy.sparse.csr_matrix): the resolution matrix in CSR format.

    """
    def __init__(self, wave, flux, ivar, R, Rcsr):
        self.nwave = wave.size
        self.wave = wave
        self.flux = flux
        self.ivar = ivar
        self.R = R
        self.Rcsr = Rcsr
        self._mpshared = False
        self._mpshmem = None
        self.wavehash = hash((len(wave), wave[0], wave[1], wave[-2], wave[-1]))

    def sharedmem_pack(self):
        """Pack spectral data into multiprocessing shared memory.
        """
        if not self._mpshared:
            if self._mpshmem is None:
                self._mpshmem = dict()
                self._mpshmem['wave'] = mpsharedmem.fromarray(self.wave)
                self._mpshmem['flux'] = mpsharedmem.fromarray(self.flux)
                self._mpshmem['ivar'] = mpsharedmem.fromarray(self.ivar)
                self._mpshmem['R.data'] = mpsharedmem.fromarray(self.R.data)
                self._mpshmem['R.offsets'] = self.R.offsets
                self._mpshmem['R.shape'] = self.R.shape
                self._mpshmem['Rcsr.indices'] = mpsharedmem.fromarray(self.Rcsr.indices)
                self._mpshmem['Rcsr.indptr'] = mpsharedmem.fromarray(self.Rcsr.indptr)
                self._mpshmem['Rcsr.data'] = mpsharedmem.fromarray(self.Rcsr.data)
                self._mpshmem['Rcsr.shape'] = self.Rcsr.shape
            
            del self.wave
            del self.flux
            del self.ivar
            del self.R
            del self.Rcsr

            self._mpshared = True
            
        return

    def sharedmem_unpack(self):
        """Unpack spectral data from multiprocessing shared memory.
        """
        if self._mpshared:
            self.wave = mpsharedmem.toarray(self._mpshmem['wave'])
            self.flux = mpsharedmem.toarray(self._mpshmem['flux'])
            self.ivar = mpsharedmem.toarray(self._mpshmem['ivar'])

            Rdata = mpsharedmem.toarray(self._mpshmem['R.data'])
            Roffsets = self._mpshmem['R.offsets']
            Rshape = self._mpshmem['R.shape']
            self.R = scipy.sparse.dia_matrix((Rdata, Roffsets),
                shape=Rshape, copy=False)

            data = mpsharedmem.toarray(self._mpshmem['Rcsr.data'])
            indices = mpsharedmem.toarray(self._mpshmem['Rcsr.indices'])
            indptr = mpsharedmem.toarray(self._mpshmem['Rcsr.indptr'])
            shape = self._mpshmem['Rcsr.shape']
            
            self.Rcsr = scipy.sparse.csr_matrix((data, indices, indptr),
                shape=shape, copy=False)

            self._mpshared = False
        return


class Target(object):
    """A single target.

    This represents the data for a single target, including a unique identifier
    and the individual spectra observed for this object (or a coadd).

    Args:
        targetid (int or str): unique targetid
        spectra (list): list of Spectrum objects
        coadd (bool): compute and store the coadd at construction time.
            The coadd can always be recomputed with the compute_coadd()
            method.
        meta (dict): optional metadata dictionary for this Target.

    """
    def __init__(self, targetid, spectra, coadd=False, meta=dict()):
        self.id = targetid
        self.spectra = spectra
        self.meta = meta
        if coadd:
            self.compute_coadd()

    def compute_coadd(self):
        """Compute the coadd from the current spectra list.

        This method REPLACES the list of individual spectra with coadds.
        """
        coadd = list()
        for key in set([s.wavehash for s in self.spectra]):
            wave = None
            unweightedflux = None
            weightedflux = None
            weights = None
            R = None
            nspec = 0
            for s in self.spectra:
                if s.wavehash != key: continue
                nspec += 1
                if weightedflux is None:
                    wave = s.wave
                    unweightedflux = np.copy(s.flux)
                    weightedflux = s.flux * s.ivar
                    weights = np.copy(s.ivar)
                    n = len(s.ivar)
                    W = scipy.sparse.dia_matrix((s.ivar, [0,]), (n,n))
                    weightedR = W * s.R
                else:
                    assert len(s.ivar) == n
                    unweightedflux += s.flux
                    weightedflux += s.flux * s.ivar
                    weights += s.ivar
                    W = scipy.sparse.dia_matrix((s.ivar, [0,]), (n,n))
                    weightedR += W * s.R

            isbad = (weights == 0)
            flux = weightedflux / (weights + isbad)
            flux[isbad] = unweightedflux[isbad] / nspec
            Winv = scipy.sparse.dia_matrix((1/(weights+isbad),
                [0,]), (n,n))
            R = Winv * weightedR
            R = R.todia()
            Rcsr = R.tocsr()
            spc = Spectrum(wave, flux, weights, R, Rcsr)
            coadd.append(spc)
        # swap the coadds into place.
        self.spectra = coadd
        return

    def sharedmem_pack(self):
        """Pack all spectra into multiprocessing shared memory.
        """
        for s in self.spectra:
            s.sharedmem_pack()
        return

    def sharedmem_unpack(self):
        """Unpack all spectra from multiprocessing shared memory.
        """
        for s in self.spectra:
            s.sharedmem_unpack()
        return


class DistTargets(object):
    """Base class for distributed targets.

    Target objects are distributed across the processes in an MPI
    communicator, but the details of how this data is loaded from disk
    is specific to a given project.  Each project should inherit from this
    base class and create an appropriate class for the data files being
    used.

    This class defines some general methods and the API that should be
    followed by these derived classes.

    Args:
        targetids (list): the global set of target IDs.
        comm (mpi4py.MPI.Comm): (optional) the MPI communicator.

    """
    def __init__(self, targetids, comm=None):
        self._comm = comm
        self._targetids = targetids
        self._dwave = None

    @property
    def comm(self):
        return self._comm

    @property
    def all_target_ids(self):
        return self._targetids


    def _local_target_ids(self):
        raise NotImplementedError("You should not instantiate a DistTargets "
            "object directly")
        return None


    def local_target_ids(self):
        """Return the local list of target IDs.
        """
        return self._local_target_ids()


    def _local_data(self):
        raise NotImplementedError("You should not instantiate a DistTargets "
            "object directly")
        return None


    def local(self):
        """Return the local list of Target objects.
        """
        return self._local_data()


    def wavegrids(self):
        """Return the global dictionary of wavelength grids for each wave hash.
        """
        if self._dwave is None:
            my_dwave = dict()
            for t in self.local():
                for s in t.spectra:
                    if s.wavehash not in my_dwave:
                        my_dwave[s.wavehash] = s.wave.copy()
            if self._comm is None:
                self._dwave = my_dwave.copy()
            else:
                temp = self._comm.allgather(my_dwave)
                self._dwave = dict()
                for pdata in temp:
                    for k, v in pdata.items():
                        if k not in self._dwave:
                            self._dwave[k] = v.copy()
                del temp
            del my_dwave

        return self._dwave


def distribute_targets(targets, nproc):
    """Distribute a list of targets among processes.

    Given a list of Target objects, compute the load balanced
    distribution of those targets among a set of processes.

    This function is used when one already has a list of Target objects that
    need to be distributed.  This happens, for example, when creating
    a DistTargetsCopy object from pre-existing Targets, or when using
    multiprocessing to do operations on the MPI-local list of targets.

    Args:
        targets (list): list of Target objects.
        nproc (int): number of processes.

    Returns:
        list:  A list (one element for each process) with each element
            being a list of the target IDs assigned to that process.

    """
    # We weight each target by the number of spectra.
    ids = list()
    tweights = dict()
    for tg in targets:
        ids.append(tg.id)
        tweights[tg.id] = len(tg.spectra)
    return distribute_work(nproc, ids, weights=tweights)


class DistTargetsCopy(DistTargets):
    """Distributed targets built from a copy.

    This class is a simple wrapper that distributes targets located on
    one process to the processes in a communicator.

    Args:
        targets (list): list of Target objects on one process.
        comm (mpi4py.MPI.Comm): (optional) the MPI communicator.
        root (int): the process which has the input targets locally.

    """

    def __init__(self, targets, comm=None, root=0):

        comm_size = 1
        comm_rank = 0
        if comm is not None:
            comm_size = comm.size
            comm_rank = comm.rank

        self._alltargetids = list()
        if comm_rank == root:
            for tg in targets:
                self._alltargetids.append(tg.id)
            self._alltargetids = sorted(self._alltargetids)

        if comm is not None:
            self._alltargetids = comm.bcast(self._alltargetids, root=root)

        # Distribute the targets among process weighted by the amount of work
        # to do for each target.

        self._proc_targets = distribute_targets(targets, comm_size)

        self._my_targets = self._proc_targets[comm_rank]

        # Distribute targets from the root process to the others

        self._my_data = None
        if comm is None:
            self._my_data = targets
        else:
            tbuf = dict()
            for tg in targets:
                recv = comm.bcast(tg, root=root)
                if recv.id in self._my_targets:
                    tbuf[recv.id] = recv
            self._my_data = [ tbuf[x] for x in self._my_targets ]

        super(DistTargetsCopy, self).__init__(self._alltargetids, comm=comm)


    def _local_target_ids(self):
        return self._my_targets

    def _local_data(self):
        return self._my_data
